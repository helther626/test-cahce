from Backend.helper.global_search import global_search, is_global_search_enabled
from Backend.helper.metadata.providers.cinemeta import get_detail, get_season
from Backend.helper.metadata import resolve_cover_url, COMBINED_SEASON, COMBINED_EPISODE_BASE
from Backend.helper.split_files import parse_combined_episodes, combined_name_key
from Backend.helper.settings_manager import SettingsManager
from Backend.helper.subtitles import get_subtitles_for, stremio_subtitle_entries
from Backend.logger import LOGGER
from Backend.pyrofork.bot import StreamBot, get_streambot_url

router = APIRouter(prefix="/stremio", tags=["Stremio Addon"])
templates = Jinja2Templates(directory="Backend/fastapi/templates")

#----- Addon configuration
ADDON_NAME = "Muvee"
ADDON_VERSION = __version__
PAGE_SIZE = 15