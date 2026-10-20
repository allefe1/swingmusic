from flask_openapi3 import Tag
from natsort import natsorted
from pydantic import BaseModel, Field
from flask_openapi3 import APIBlueprint

from datetime import datetime, timezone
from swingmusic.config import UserConfig
from swingmusic.store.albums import AlbumStore
from swingmusic.store.artists import ArtistStore
from swingmusic.store.tracks import TrackStore
from swingmusic.api.apischemas import GenericLimitSchema

from swingmusic.utils import format_number
from swingmusic.utils.dates import (
    create_new_date,
    date_string_to_time_passed,
    seconds_to_time_string,
    timestamp_to_time_passed,
)
from swingmusic.utils.parsers import get_sort_name
from swingmusic.serializers.album import serialize_for_card as serialize_album
from swingmusic.serializers.artist import serialize_for_card as serialize_artist
from swingmusic.serializers.track import serialize_tracks

bp_tag = Tag(name="Get all", description="List all items")
api = APIBlueprint("getall", __name__, url_prefix="/getall", abp_tags=[bp_tag])


class GetAllItemsQuery(GenericLimitSchema):
    start: int = Field(
        description="The start index of the items to return",
        example=0,
        default=0,
    )
    sortby: str = Field(
        description="The key to sort items by",
        example="created_date",
        default="created_date",
    )

    reverse: str = Field(
        description="Reverse the sort",
        example=1,
        default="1",
    )


class GetAllItemsPath(BaseModel):
    itemtype: str = Field(
        description="The type of items to return (albums | artists | tracks)",
        example="albums",
        default="albums",
    )


COMMON_SORT_KEYS = {
    "date",
    "created_date",
    "trackcount",
    "duration",
    "playcount",
    "playduration",
    "lastplayed",
}
ALBUM_SORT_KEYS = COMMON_SORT_KEYS | {"title", "albumartists"}
ARTIST_SORT_KEYS = COMMON_SORT_KEYS | {"name", "albumcount"}
TRACK_SORT_KEYS = COMMON_SORT_KEYS | {"title", "album", "artists"}
TEXT_SORT_KEYS = {"title", "albumartists", "name", "album", "artists"}


def resolve_sort_key(sortkey: str, itemtype: str) -> str:
    """
    Returns the sort key if it is valid for the item type, else the default text key.
    """
    if itemtype == "albums":
        allowed = ALBUM_SORT_KEYS
        default = "title"
    elif itemtype == "artists":
        allowed = ARTIST_SORT_KEYS
        default = "name"
    else:
        allowed = TRACK_SORT_KEYS
        default = "title"

    if sortkey in allowed:
        return sortkey

    return default


def build_sort_key(sortkey: str, itemtype: str, config: UserConfig):
    """
    Returns a total sort key function for the given attribute that never raises.
    """
    is_text = sortkey in TEXT_SORT_KEYS
    if itemtype == "albums":
        article_key = "albumartists"
    elif itemtype == "artists":
        article_key = "name"
    else:
        article_key = "title"

    article_aware = sortkey == article_key and config.artistArticleAwareSorting
    articles = config.artistSortingArticles

    def sort_key(x):
        value = getattr(x, sortkey, None)

        if sortkey in ("albumartists", "artists"):
            value = value[0].get("name", "") if value else ""

        if is_text:
            name = value if isinstance(value, str) else str(value or "")

            if article_aware:
                name = get_sort_name(name, articles=articles)

            return name.casefold()

        if isinstance(value, (int, float)):
            return value

        try:
            return float(value)
        except (TypeError, ValueError):
            return 0

    return sort_key


@api.get("/<itemtype>")
def get_all_items(path: GetAllItemsPath, query: GetAllItemsQuery):
    """
    Get all items

    Used to show all albums, artists or tracks in the library

    Sort keys:
    -
    Common: `duration`, `created_date`, `playcount`, `playduration`, `lastplayed`

    Albums only: `title`, `albumartists`, `date`, `trackcount`
    Artists only: `name`, `albumcount`, `trackcount`
    Tracks only: `title`, `album`, `artists`, `date`
    """
    is_albums = path.itemtype == "albums"
    is_artists = path.itemtype == "artists"
    is_tracks = path.itemtype == "tracks"

    if is_albums:
        items = AlbumStore.get_flat_list()
    elif is_artists:
        items = ArtistStore.get_flat_list()
    elif is_tracks:
        items = [group.get_best() for group in TrackStore.trackhashmap.values()]
    else:
        return {
            "error": "Invalid item type. Valid types are 'albums', 'artists' and 'tracks'"
        }, 400

    total = len(items)

    start = query.start
    limit = query.limit
    sortkey = resolve_sort_key(query.sortby, path.itemtype)
    reverse = query.reverse == "1"

    sort_is_count = sortkey == "trackcount"
    sort_is_duration = sortkey == "duration"
    sort_is_create_date = sortkey == "created_date"
    sort_is_playcount = sortkey == "playcount"
    sort_is_playduration = sortkey == "playduration"
    sort_is_lastplayed = sortkey == "lastplayed"

    sort_is_date = (is_albums or is_tracks) and sortkey == "date"

    sort_is_artist_trackcount = is_artists and sortkey == "trackcount"
    sort_is_artist_albumcount = is_artists and sortkey == "albumcount"

    sort_key = build_sort_key(sortkey, path.itemtype, UserConfig())

    if sortkey in TEXT_SORT_KEYS:
        sorted_items = natsorted(items, key=sort_key, reverse=reverse)
    else:
        sorted_items = sorted(items, key=sort_key, reverse=reverse)

    items = sorted_items[start : start + limit]

    if is_tracks:
        return {"items": serialize_tracks(items), "total": total}
    album_list = []

    for item in items:
        item_dict = serialize_album(item) if is_albums else serialize_artist(item)

        if sort_is_date:
            item_dict["help_text"] = datetime.fromtimestamp(
                item.date, tz=timezone.utc
            ).year

        if sort_is_create_date:
            date = create_new_date(datetime.fromtimestamp(item.created_date))
            timeago = date_string_to_time_passed(date)
            item_dict["help_text"] = timeago

        if sort_is_count:
            item_dict["help_text"] = (
                f"{format_number(item.trackcount)} track{'' if item.trackcount == 1 else 's'}"
            )

        if sort_is_duration:
            item_dict["help_text"] = seconds_to_time_string(item.duration)

        if sort_is_artist_trackcount:
            item_dict["help_text"] = (
                f"{format_number(item.trackcount)} track{'' if item.trackcount == 1 else 's'}"
            )

        if sort_is_artist_albumcount:
            item_dict["help_text"] = (
                f"{format_number(item.albumcount)} album{'' if item.albumcount == 1 else 's'}"
            )

        if sort_is_playcount:
            item_dict["help_text"] = (
                f"{format_number(item.playcount)} play{'' if item.playcount == 1 else 's'}"
            )

        if sort_is_lastplayed:
            if item.playduration == 0:
                item_dict["help_text"] = "Never played"
            else:
                item_dict["help_text"] = timestamp_to_time_passed(item.lastplayed)

        if sort_is_playduration:
            item_dict["help_text"] = seconds_to_time_string(item.playduration)

        album_list.append(item_dict)

    return {"items": album_list, "total": total}
