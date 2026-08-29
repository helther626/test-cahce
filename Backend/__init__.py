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

__version__ = "5.0.1"


#----- Install the auto-catalog manual override layer after Database and the
#----- original auto-catalog functions are loaded.
import Backend.helper.catalog_manual_overrides  # noqa: F401,E402

#----- Extend metadata relink so catalog references and subtitle identities
#----- follow a manually corrected TMDB/IMDb identity.
import Backend.helper.media_relink_overrides  # noqa: F401,E402

#----- Keep the Stremio stream UI label concise without changing stream URLs,
#----- Telegram file IDs, quality filtering, or sorting.
import Backend.helper.stream_label_overrides  # noqa: F401,E402
