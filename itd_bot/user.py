from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import Bot
    from .post import Post


@dataclass(frozen=True)
class User:
    """Profile fields returned by ITD, with the original response available in raw."""

    id: str
    username: str | None
    display_name: str | None
    avatar: Any
    banner: Any
    bio: str | None
    is_verified: bool | None
    is_private: bool | None
    pin: Any
    has_nuksta: bool | None
    pinned_post_id: str | None
    wall_access: str | None
    likes_visibility: str | None
    followers_count: int | None
    following_count: int | None
    posts_count: int | None
    is_following: bool | None
    is_followed_by: bool | None
    created_at: str | None
    can_message: bool | None
    online: bool | None
    last_seen: Any
    raw: dict[str, Any]
    _bot: Bot | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, bot: Bot | None = None) -> User:
        from .errors import ProtocolError

        if (
            not isinstance(data, dict)
            or not isinstance(data.get("id"), str)
            or not data["id"]
        ):
            raise ProtocolError("Invalid user profile response")
        return cls(
            id=data["id"],
            username=data.get("username"),
            display_name=data.get("displayName"),
            avatar=data.get("avatar"),
            banner=data.get("banner"),
            bio=data.get("bio"),
            is_verified=data.get("isVerified", data.get("verified")),
            is_private=data.get("isPrivate"),
            pin=data.get("pin"),
            has_nuksta=data.get("hasNuksta"),
            pinned_post_id=data.get("pinnedPostId"),
            wall_access=data.get("wallAccess"),
            likes_visibility=data.get("likesVisibility"),
            followers_count=data.get("followersCount"),
            following_count=data.get("followingCount"),
            posts_count=data.get("postsCount"),
            is_following=data.get("isFollowing"),
            is_followed_by=data.get("isFollowedBy"),
            created_at=data.get("createdAt"),
            can_message=data.get("canMessage"),
            online=data.get("online"),
            last_seen=data.get("lastSeen"),
            raw=dict(data),
            _bot=bot,
        )

    @property
    def banner_url(self) -> str | None:
        if isinstance(self.banner, str):
            return self.banner or None
        if isinstance(self.banner, dict):
            value = self.banner.get("url")
            return value if isinstance(value, str) and value else None
        return None

    def download_banner(self, destination: str | Path) -> Path:
        if self._bot is None:
            raise RuntimeError("This user is not associated with a bot")
        if self.banner_url is None:
            raise ValueError("User has no banner URL")
        return self._bot.download_banner(self.banner_url, destination)

    @property
    def pinned_post(self) -> Post | None:
        if not self.pinned_post_id:
            return None
        if self._bot is None:
            raise RuntimeError("This user is not associated with a bot")
        return self._bot.get_post(self.pinned_post_id)
