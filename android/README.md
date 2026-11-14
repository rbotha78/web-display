# Android manager

The native manager connects directly to enrolled displays using pinned HTTPS and
bearer API keys. **More → Music sync groups** lists known groups, creates a group,
dissolves groups, and removes individual followers. Device cards reserve one line
for music sync state. Refresh reads `/api/music/sync`; old firmware (404) is
unsupported, while failed or malformed reads show unknown. Offline, unsupported,
untrusted and unknown-state displays cannot be selected for a new group.

Choose one leader and at least one follower. Followers play the leader's music
and ignore their own music settings. All displays must use the same Plex server;
the manager cannot verify this in advance, so connection/Plex errors appear in
the group list. Controls on any display affect the whole group; volume, video
audio and sleep remain per display.

Creation confirms role changes and offers to dissolve selected displays' old
groups first, including their unselected enrolled members. It sends the leader
first and aborts if that fails, then attempts each follower. Dissolving attempts
all known followers before the leader, even if a follower fails. Results are
reported per display; partial success is not automatically rolled back. Group
credentials are generated with SecureRandom and kept only during the operation,
never saved, logged or shown.

Groups are derived from the latest known states of enrolled displays; unknown or
offline states do not retain stale memberships. Leader counts reflect followers
seen in the last 60 seconds, which may include unenrolled displays. Recently seen
names are informational (not device identities). Unenrolled or unknown-state
members cannot be cleared here; reconnect/refresh, enroll them, or clear their
roles separately. A group with no known leader can only clear its known followers.

Build and validate with Java 17 and the Android SDK:

```sh
ANDROID_HOME="$HOME/Android/Sdk" ./android/gradlew --no-daemon -p android testDebugUnitTest lintDebug assembleDebug
```

The debug APK is `android/app/build/outputs/apk/debug/web-display-manager.apk`.
