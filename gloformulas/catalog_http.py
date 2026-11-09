"""Catalog-only pacing and private, bounded transport diagnostics. No endpoint fallback."""
import random
import math
import time
from datetime import datetime, timedelta, timezone
from .http import Client, FetchError, retry_after_seconds

MIN_INTERVAL=5.0
MAX_LOCAL_BACKOFF=7*24*3600
HEADER_NAMES={'retry-after','date','ratelimit-limit','ratelimit-remaining','ratelimit-reset',
              'x-ratelimit-limit','x-ratelimit-remaining','x-ratelimit-reset','server','cf-mitigated'}

def robots_interval(content,agent):
    """Read pacing extensions for the most specific matching user-agent groups, including decimal delays."""
    groups=[];agents=[];rules=[]
    for raw in content.splitlines()+['']:
        line=raw.split('#',1)[0].strip()
        if not line:
            if agents:groups.append((agents,rules))
            agents=[];rules=[];continue
        name,separator,value=line.partition(':')
        if not separator:continue
        name=name.strip().lower();value=value.strip()
        if name=='user-agent':
            if rules:groups.append((agents,rules));agents=[];rules=[]
            agents.append(value.lower())
        elif agents:rules.append((name,value))
    token=agent.split('/',1)[0].lower()
    matching=[]
    for agents,rules in groups:
        scores=[0 if a=='*' else len(a) for a in agents if a=='*' or (a and a in token)]
        if scores:matching.append((max(scores),rules))
    highest=max((score for score,_ in matching),default=-1);interval=0
    for score,rules in matching:
        if score!=highest:continue
        for name,value in rules:
            if name not in ('crawl-delay','request-rate'):continue
            try:
                if name=='crawl-delay':delay=float(value)
                else:
                    count,seconds=value.split('/')
                    if not count.strip().isdigit() or int(count)<=0:raise ValueError
                    seconds=float(seconds)
                    if seconds<=0:raise ValueError
                    delay=seconds/int(count)
                if not math.isfinite(delay) or delay<0:raise ValueError
                interval=max(interval,delay)
            except (ValueError,OverflowError,ZeroDivisionError):
                raise FetchError('Catalog robots pacing rule requires manual review') from None
    return interval

class CatalogClient(Client):
    def __init__(self,hosts):
        self.stage='robots';self.events=[];self.request_count=0;self.blocked=False;self.guard=None
        self.started=time.monotonic();self.last_status=None
        self.server_delay=None
        self.trace_truncated=False
        # One attempt also conservatively stops on other transport failures. Other clients retain their defaults.
        super().__init__(hosts,attempts=1,interval=MIN_INTERVAL,observer=self.observe,
                         request_guard=self.check_policy,pace_redirects=True)

    def check_policy(self,url):
        if self.blocked:raise FetchError('Catalog scan stopped after HTTP 429',429)
        if self.guard:self.guard(url)
        # Never sleep through the source step's execution budget or ignore a long published delay.
        if self.interval>60:raise FetchError('Catalog robots pacing exceeds this scan budget; manual review required')

    def observe(self,event,url,status,headers):
        if event=='request':
            self.request_count+=1
            self.last_status=None
        if status is not None:self.last_status=status
        safe={str(k).lower():str(v)[:256] for k,v in headers.items() if str(k).lower() in HEADER_NAMES}
        if status==429:
            self.blocked=True
            # Parse the full instruction; truncation is only for retained diagnostic text.
            raw_retry=next((str(v) for k,v in headers.items() if str(k).lower()=='retry-after'),'').strip()
            self.server_delay=retry_after_seconds(raw_retry,round_up=True)
            if raw_retry.isdigit() and len(raw_retry)>12:self.server_delay=10**13
        if len(self.events)>=1000:
            self.events.pop(0);self.trace_truncated=True
        self.events.append({'event':event,'stage':self.stage,'url':url,'status':status,
            'request_count':self.request_count,'elapsed_seconds':round(time.monotonic()-self.started,3),
            'headers':safe})

    def diagnostics(self):
        return {'stage':self.stage,'request_count':self.request_count,'http_status':self.last_status,
                'interval_seconds':self.interval,'server_retry_after_seconds':self.server_delay,
                'trace_truncated':self.trace_truncated,'requests':self.events}

def register_limit(db,server_delay,moment=None,jitter=None):
    """Positive bounded jitter never shortens either the server's or our own minimum."""
    moment=moment or datetime.now(timezone.utc)
    row=db.execute('SELECT consecutive_429 FROM catalog_rate_state WHERE id=1').fetchone()
    streak=(row[0] if row else 0)+1
    base=min(MAX_LOCAL_BACKOFF,24*3600*2**min(streak-1,3))
    extra=(jitter or random.uniform)(0,min(3600,base*.1))
    extra=max(0,min(3600,base*.1,float(extra)))
    local=min(MAX_LOCAL_BACKOFF,base+extra)
    delay=max(local,server_delay if server_delay is not None else 0)
    try:retry=(moment+timedelta(seconds=delay)).isoformat()
    except OverflowError:
        # An unrepresentably long server instruction must never become an early retry.
        retry=datetime.max.replace(tzinfo=timezone.utc).isoformat()
        delay=(datetime.max.replace(tzinfo=timezone.utc)-moment).total_seconds()
    db.execute('INSERT INTO catalog_rate_state VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET consecutive_429=excluded.consecutive_429,retry_at=excluded.retry_at,delay_seconds=excluded.delay_seconds',
               (streak,retry,delay))
    db.execute("INSERT INTO source_backoff VALUES('catalog',?,'HTTP 429') ON CONFLICT(component) DO UPDATE SET retry_at=excluded.retry_at,reason=excluded.reason",(retry,))
    return {'retry_at':retry,'cooldown_seconds':delay,'consecutive_429':streak}

def reset_limits(db):
    db.execute('DELETE FROM catalog_rate_state WHERE id=1')
    db.execute("DELETE FROM source_backoff WHERE component='catalog'")
