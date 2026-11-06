"""Source IDs belong in private runtime configuration, not public git history."""
import json,os
from pathlib import Path
path=Path(os.environ.get('GLO_SOURCE_CONFIG','work/source-config.json'))
config=json.loads(path.read_text()) if path.exists() else {}
GUIDE_URL=config.get('GUIDE_URL','https://docs.google.com/document/d/CONFIGURE_GUIDE_ID/edit')
SHEET_URL=config.get('SHEET_URL','https://docs.google.com/spreadsheets/d/CONFIGURE_SHEET_ID/edit')
FOLDER_URL=config.get('FOLDER_URL','https://drive.google.com/drive/folders/CONFIGURE_FOLDER_ID')
