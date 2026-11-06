"""Read-only catalog adapters. Completeness is scoped, recorded and never guessed."""
import html
import json
import re
import urllib.parse as url
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from .http import Client, FetchError, AGENT
import hashlib
from .core import key, now
from .catalog import validate

BASE = 'https://gloformulas.com'
HOSTS = {'gloformulas.com','www.gloformulas.com'}

class Page(HTMLParser):
    def __init__(self, content):
        super().__init__(); self.scripts=[];self.links=[];self.anchors=[];self.forms=[];self._script=None;self._form=None;self._anchor=None
        self.feed(content)
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='script' and a.get('type','').split(';')[0]=='application/ld+json': self._script=[]
        if tag=='a' and a.get('href'):
            self.links.append(a['href']);self._anchor=[a['href'],[]]
        if tag=='form': self._form=dict(a,inputs=[])
        if tag in ('input','select','button') and self._form is not None: self._form['inputs'].append(a)
    def handle_data(self, data):
        if self._anchor is not None:self._anchor[1].append(data)
        if self._script is not None:self._script.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self._anchor is not None:
            self.anchors.append((self._anchor[0],' '.join(self._anchor[1])));self._anchor=None
        if tag=='script' and self._script is not None:
            self.scripts.append(''.join(self._script));self._script=None
        if tag=='form' and self._form is not None:self.forms.append(self._form);self._form=None


def objects(value):
    if isinstance(value,dict):
        yield value
        for v in value.values():yield from objects(v)
    elif isinstance(value,list):
        for v in value:yield from objects(v)


def coa_links(content,page_url):
    # These are candidate links, never verified report results or product/batch matches.
    result=set()
    for href,label in Page(content).anchors:
        target=url.urljoin(page_url,href);parsed=url.urlparse(target)
        if parsed.scheme!='https' or parsed.username or parsed.password:continue
        if parsed.path.lower().endswith('.pdf') or re.search(r'coa|certificate|lab[-_ ]?(?:test|result)|report',parsed.path+' '+label,re.I):
            result.add(target)
    return sorted(result)


def product_data(content,page_url):
    products=[]
    for script in Page(content).scripts:
        try:data=json.loads(script)
        except ValueError:raise ValueError('Malformed structured product data; scan incomplete') from None
        for obj in objects(data):
            types=obj.get('@type',[]);types=[types] if isinstance(types,str) else types
            if 'Product' not in types:continue
            if not obj.get('name'):raise ValueError('Product structured data missing name')
            offers=obj.get('offers',[]);offers=[offers] if isinstance(offers,dict) else offers
            sizes=set()
            for size in (obj.get('size'),):
                if isinstance(size,str):sizes.add(size)
                elif isinstance(size,list):sizes.update(s for s in size if isinstance(s,str))
            # Preserve label units; do not equate mass, IU, concentration or blend ratios.
            for value in [obj.get('name','')]+[str(o.get('name','')) for o in offers]:
                sizes.update(re.findall(r'\b\d+(?:\.\d+)?\s*(?:mg|mcg|IU|mL)\b',value,re.I))
            product_url=url.urljoin(page_url,obj.get('url') or page_url)
            products.append({'key':str(obj.get('productID') or obj.get('sku') or product_url),'name':html.unescape(obj['name']), 'url':product_url,'sizes':sorted(sizes), 'availability':sorted({str(o['availability']).rsplit('/',1)[-1] for o in offers if o.get('availability')}),'listing_hash':key(obj),'coa_links':coa_links(content,page_url)})
    return products


class CatalogCrawler:
    def __init__(self,client=None,base=BASE,max_pages=300):
        self.client=client or Client(HOSTS);self.base=base.rstrip('/');self.max_pages=max_pages;self.observations=[];self.robot=None
    def fetch(self,path):
        target=url.urljoin(self.base+'/',path)
        if url.urlparse(target).hostname not in HOSTS:raise ValueError('Catalog link left the source host')
        if self.robot is not None and not self.robot.can_fetch(AGENT,target):raise FetchError('Catalog path disallowed by robots.txt')
        r=self.client.get(target)
        self.observations.append({'url':r.url,'sha256':hashlib.sha256(r.body).hexdigest(),'status':r.status})
        return r
    def robots(self):
        r=self.client.get(self.base+'/robots.txt')
        robot=urllib.robotparser.RobotFileParser();robot.parse(r.text.splitlines());self.robot=robot
        delay=robot.crawl_delay(AGENT) or robot.crawl_delay('*')
        if delay is not None and hasattr(self.client,'interval'):self.client.interval=max(self.client.interval,delay)
        return [s.split(':',1)[1].strip() for s in r.text.splitlines() if s.lower().startswith('sitemap:')]
    def run(self,mode='auto'):
        try:sitemaps=self.robots()
        except FetchError as exc:
            if exc.status!=404:raise
            sitemaps=[]
        if mode not in ('auto','woocommerce','shopify','sitemap'):raise ValueError('Unknown catalog adapter')
        if mode=='auto':
            home=self.fetch('/').text
            if 'wp-content/' in home or 'woocommerce' in home.lower():mode='woocommerce'
            elif 'cdn.shopify.com' in home or 'Shopify.theme' in home:mode='shopify'
            else:mode='sitemap'
        products=[]
        seen_keys=set()
        if mode=='woocommerce':
            total=None
            for page in range(1,self.max_pages+1):
                r=self.fetch(f'/wp-json/wc/store/v1/products?per_page=100&page={page}')
                data=r.json()
                if not isinstance(data,list):raise ValueError('Unrecognized WooCommerce catalog response')
                if 'x-wp-total' not in r.headers:raise ValueError('Catalog total missing; cannot prove pagination complete')
                current_total=int(r.headers['x-wp-total'])
                if total is not None and total!=current_total:raise ValueError('Catalog changed during pagination; retry later')
                total=current_total
                for p in data:
                    if str(p['id']) in seen_keys:raise ValueError('Catalog pagination repeated a product')
                    seen_keys.add(str(p['id']))
                    sizes=[str(term.get('name','') if isinstance(term,dict) else term) for a in p.get('attributes',[]) if re.search('size|strength|amount',a.get('name',''),re.I) for term in a.get('terms',[])]
                    products.append({'key':str(p['id']),'name':html.unescape(p['name']),'url':p['permalink'],'sizes':sorted(sizes),'availability':'in_stock' if p.get('is_in_stock') is True else ('out_of_stock' if p.get('is_in_stock') is False else 'unknown'),'listing_hash':key(p)})
                if len(products)==total:break
                if not data or len(products)>total:raise ValueError('Catalog pagination totals inconsistent')
            else:raise ValueError('Catalog page limit reached')
            scope='woocommerce-store-api'
        elif mode=='shopify':
            for page in range(1,self.max_pages+1):
                data=self.fetch(f'/products.json?limit=250&page={page}').json()
                if not isinstance(data,dict) or not isinstance(data.get('products'),list):raise ValueError('Unrecognized Shopify response')
                rows=data['products']
                for p in rows:
                    if str(p['id']) in seen_keys:raise ValueError('Catalog pagination repeated a product')
                    seen_keys.add(str(p['id']))
                    variants=p.get('variants',[])
                    products.append({'key':str(p['id']),'name':p['title'],'url':self.base+'/products/'+p['handle'],'sizes':sorted({v['title'] for v in variants if v.get('title') and v['title']!='Default Title'}),'availability':sorted({str(v.get('available','unknown')) for v in variants}),'listing_hash':key(p)})
                if not rows:break
            else:raise ValueError('Catalog pagination did not terminate')
            scope='shopify-public-products'
        else:
            pending=list(sitemaps or [self.base+'/sitemap.xml']);visited=set();pages=set()
            while pending:
                target=pending.pop(0)
                if target in visited:continue
                if len(visited)+len(pages)>=self.max_pages:raise ValueError('Sitemap page limit reached')
                visited.add(target);root=ET.fromstring(self.fetch(target).body)
                kind=root.tag.rsplit('}',1)[-1]
                locs=[e.text for e in root.iter() if e.tag.rsplit('}',1)[-1]=='loc' and e.text]
                if kind=='sitemapindex':pending.extend(locs)
                elif kind=='urlset':pages.update(locs)
                else:raise ValueError('Unrecognized sitemap document')
            if not pages or len(pages)>self.max_pages:raise ValueError('Missing or oversized sitemap scope')
            for target in sorted(pages):
                content=self.fetch(target).text;rows=product_data(content,target)
                if not rows and re.search(r'/products?/[^/]+',url.urlparse(target).path):raise ValueError('Product page missing readable structured data; rendered browser adapter needed')
                products.extend(rows)
            scope='published-sitemap-products'
        if mode in ('woocommerce','shopify'):
            if len(products)+len(self.observations)>self.max_pages:raise ValueError('Product/COA page budget reached; scan incomplete')
            for p in products:
                page=self.fetch(p['url']).text
                p['coa_links']=coa_links(page,p['url'])
        # Multiple pages may repeat the same Product; conflicting repetitions invalidate the scan.
        unique={}
        for p in products:
            if p['key'] in unique and unique[p['key']]!=p:raise ValueError('Conflicting duplicate products in crawl')
            unique[p['key']]=p
        payload={'complete':True,'observed_at':now(),'scope':scope,'products':sorted(unique.values(),key=lambda p:p['key']),'crawl':self.observations}
        validate(payload)
        return payload
