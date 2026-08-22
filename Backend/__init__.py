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

#----- Install the auto-catalog manual override layer after Database and the
#----- original auto-catalog functions are loaded. Do not silently ignore
#----- installation errors: a broken override must fail startup rather than
#----- leave catalog behavior half-installed.
import Backend.helper.catalog_manual_overrides  # noqa: F401,E402

#----- Extend the existing metadata relink operation so catalog references
#----- and subtitle identities follow the new TMDB/IMDb identity too.
import Backend.helper.media_relink_overrides  # noqa: F401,E402

__version__ = "5.0.1"

#----- Keep the Stremio stream UI label concise without changing stream URLs,
#----- Telegram file IDs, quality filtering, or sorting.
import Backend.helper.stream_label_overrides  # noqa: F401,E402
