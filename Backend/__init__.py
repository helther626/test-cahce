from datetime import datetime
from time import time

import pytz

from Backend.helper.database import Database

#----- Shared application state
timezone = pytz.timezone("Asia/Kolkata")
now = datetime.now(timezone)
StartTime = time()

USE_DEFAULT_ID: str = None
MANUAL_SESSION: dict = None
db = Database()

#----- Install the auto-catalog manual override layer after Database is loaded.
#----- It only extends catalog reads/add/remove/purge behavior and does not
#----- alter storage documents or the existing auto-classification code.
try:
    import Backend.helper.catalog_manual_overrides  # noqa: F401
except Exception:
    #----- Keep startup resilient; catalog functionality remains on the
    #----- original implementation if the optional compatibility layer fails.
    pass

__version__ = "5.0.1"
