import { definePlugin, addEventListener, removeEventListener } from "@decky/api";
import {
  ButtonItem,
  DialogButton,
  DropdownItem,
  Field,
  Focusable,
  PanelSection,
  PanelSectionRow,
  SliderField,
  staticClasses,
} from "@decky/ui";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  FaBackward,
  FaForward,
  FaHeart,
  FaMusic,
  FaPause,
  FaPlay,
  FaRandom,
  FaRegHeart,
} from "react-icons/fa";
import {
  interpolateElapsed,
  isOST,
  type Album,
  type PlayerStatus,
  type ReplayGainMode,
} from "./core";

import * as backend from "./backend";

const ACCENT = "#1a9fff";
const ELLIPSIS = {
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
} as const;

/** Subscribe to backend player events; expose status + a locally-interpolated
 *  elapsed time. The seek bar updates from a LOCAL timer — it never polls MPD. */
function useNowPlaying() {
  const [status, setStatus] = useState<PlayerStatus | null>(null);
  const [snapshotAt, setSnapshotAt] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    let alive = true;
    const apply = (s: PlayerStatus) => {
      if (!alive) return;
      setStatus(s);
      setSnapshotAt(Date.now());
    };
    // Initial snapshot, then event-driven updates only.
    backend.getStatus().then(apply).catch(() => {});
    addEventListener<[PlayerStatus]>(backend.PLAYER_EVENT, apply);
    return () => {
      alive = false;
      removeEventListener(backend.PLAYER_EVENT, apply);
    };
  }, []);

  // Local ticker for the progress bar while the panel is open (no MPD calls).
  useEffect(() => {
    if (status?.state !== "play") return;
    const id = setInterval(() => setNowMs(Date.now()), 500);
    return () => clearInterval(id);
  }, [status?.state]);

  const elapsed = status
    ? interpolateElapsed(status.elapsedSec, snapshotAt, nowMs, status.state, status.durationSec)
    : 0;

  return { status, elapsed };
}

function NowPlaying({ status, elapsed }: { status: PlayerStatus | null; elapsed: number }) {
  const current = status?.current ?? null;
  const duration = status?.durationSec ?? current?.durationSec ?? 0;
  const playing = status?.state === "play";

  // Music-only volume: MPD's software mixer, independent of the system volume,
  // so music can be balanced against game audio. Local state while dragging;
  // backend events resync it.
  const [volume, setVolumeLocal] = useState<number | null>(null);
  useEffect(() => {
    if (status?.volume != null) setVolumeLocal(status.volume);
  }, [status?.volume]);

  // Seek scrubbing: SliderField fires onChange continuously while you drag, so
  // seeking on every tick floods MPD and you hear it stutter through positions.
  // Hold the value locally and commit exactly ONE seek: on pointer release for
  // touch/mouse, or ~400ms after the last change for gamepad input (which has no
  // release event). Audio keeps playing the current spot until a clean jump.
  const [scrub, setScrub] = useState<number | null>(null);
  const pendingSeek = useRef<number | null>(null);
  const seekTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const commitSeek = () => {
    if (seekTimer.current) {
      clearTimeout(seekTimer.current);
      seekTimer.current = null;
    }
    const v = pendingSeek.current;
    if (v == null) return;
    pendingSeek.current = null;
    void backend.seek(v).finally(() => setScrub(null));
  };
  useEffect(() => () => {
    if (seekTimer.current) clearTimeout(seekTimer.current);
  }, []);

  return (
    <PanelSection title="Now Playing">
      {/* Fixed-height track block: identical footprint whether nothing is
          playing, the title is short, or everything overflows — no layout
          jumps between tracks. */}
      <PanelSectionRow>
        <div style={{ display: "flex", gap: "8px", alignItems: "center", minHeight: "40px" }}>
          <FaMusic style={{ flex: "0 0 auto", opacity: 0.7 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: "13px", lineHeight: "18px", height: "18px", ...ELLIPSIS }}>
              {current?.title ?? "Nothing playing"}
            </div>
            <div
              style={{
                fontSize: "11px",
                lineHeight: "16px",
                height: "16px",
                opacity: 0.6,
                ...ELLIPSIS,
              }}
            >
              {current ? [current.artist, current.album].filter(Boolean).join(" — ") : " "}
            </div>
          </div>
        </div>
      </PanelSectionRow>

      {/* Always rendered (disabled when idle) so the panel height is stable. */}
      <PanelSectionRow>
        <div style={{ width: "100%" }} onPointerUp={commitSeek}>
          <SliderField
            value={scrub ?? (duration > 0 ? Math.min(elapsed, duration) : 0)}
            min={0}
            max={duration > 0 ? duration : 1}
            step={1}
            disabled={duration <= 0}
            notchTicksVisible={false}
            onChange={(v) => {
              setScrub(v);
              pendingSeek.current = v;
              if (seekTimer.current) clearTimeout(seekTimer.current);
              seekTimer.current = setTimeout(commitSeek, 400);
            }}
          />
        </div>
      </PanelSectionRow>

      <PanelSectionRow>
        <Focusable style={{ display: "flex", gap: "8px", justifyContent: "space-between" }}>
          <DialogButton onClick={() => void backend.previous()} style={{ minWidth: 0, flex: 1 }}>
            <FaBackward />
          </DialogButton>
          <DialogButton onClick={() => void backend.togglePlayPause()} style={{ minWidth: 0, flex: 1 }}>
            {playing ? <FaPause /> : <FaPlay />}
          </DialogButton>
          <DialogButton onClick={() => void backend.next()} style={{ minWidth: 0, flex: 1 }}>
            <FaForward />
          </DialogButton>
          <DialogButton
            onClick={() => void backend.toggleShuffle()}
            style={{
              minWidth: 0,
              flex: 1,
              ...(status?.random
                ? { background: "rgba(26, 159, 255, 0.35)", color: ACCENT }
                : {}),
            }}
          >
            <FaRandom />
          </DialogButton>
        </Focusable>
      </PanelSectionRow>

      {volume != null && (
        <PanelSectionRow>
          <SliderField
            label="Music volume"
            description="Game audio unaffected"
            value={volume}
            min={0}
            max={100}
            step={5}
            notchTicksVisible={false}
            onChange={(v) => {
              setVolumeLocal(v);
              void backend.setVolume(v);
            }}
          />
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

// ---- library browse (drill-down; same levels as the main app) ----

type View =
  | { kind: "root" }
  | { kind: "artists" }
  | { kind: "artist"; artist: string }
  | { kind: "soundtracks" }
  | { kind: "favorites" }
  | { kind: "settings" };

/** Level chrome: a back button beside the level name (a plain label). */
function LevelHeader({ title, onBack }: { title: string; onBack: () => void }) {
  return (
    <PanelSectionRow>
      <Focusable
        style={{ display: "flex", gap: "10px", alignItems: "center", padding: "6px 0" }}
      >
        <DialogButton
          style={{
            minWidth: "54px",
            flex: "0 0 54px",
            padding: "10px 0",
            fontSize: "16px",
            lineHeight: "16px",
          }}
          onClick={onBack}
        >
          ‹
        </DialogButton>
        <div
          style={{ flex: 1, minWidth: 0, fontWeight: 600, fontSize: "14px", ...ELLIPSIS }}
        >
          {title}
        </div>
      </Focusable>
    </PanelSectionRow>
  );
}

// ---- album cover thumbnails ----------------------------------------------------
// Full-size embedded art rendered at thumb size (no backend resize available):
// fetches are serialized (one readpicture chain at a time on the MPD command
// channel) and the cache is FIFO-capped to bound base64 memory in the webview.
const COVER_CACHE_MAX = 60;
const coverCache = new Map<string, string | null>();
const coverInflight = new Map<string, Promise<string | null>>();
let coverQueue: Promise<unknown> = Promise.resolve();

function fetchCover(albumKey: string): Promise<string | null> {
  if (coverCache.has(albumKey)) return Promise.resolve(coverCache.get(albumKey) ?? null);
  let p = coverInflight.get(albumKey);
  if (!p) {
    p = (coverQueue = coverQueue.then(() => backend.albumCover(albumKey)).catch(() => null)).then(
      (url) => {
        coverInflight.delete(albumKey);
        if (coverCache.size >= COVER_CACHE_MAX) {
          coverCache.delete(coverCache.keys().next().value!);
        }
        coverCache.set(albumKey, (url as string | null) ?? null);
        return (url as string | null) ?? null;
      },
    );
    coverInflight.set(albumKey, p);
  }
  return p;
}

function AlbumThumb({ albumKey }: { albumKey: string }) {
  const [url, setUrl] = useState<string | null>(() => coverCache.get(albumKey) ?? null);
  useEffect(() => {
    let alive = true;
    fetchCover(albumKey).then((u) => {
      if (alive) setUrl(u);
    });
    return () => {
      alive = false;
    };
  }, [albumKey]);
  return (
    <div
      style={{
        flex: "0 0 28px",
        width: "28px",
        height: "28px",
        borderRadius: "3px",
        overflow: "hidden",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(255, 255, 255, 0.06)",
      }}
    >
      {url ? (
        <img src={url} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : (
        <FaMusic style={{ opacity: 0.3, fontSize: "12px" }} />
      )}
    </div>
  );
}

/** The headline act: start a whole scope as the listening session. */
function SessionPills({ albums, onPlayed }: { albums: Album[]; onPlayed: () => void }) {
  if (albums.length === 0) return null;
  const keys = albums.map((a) => a.key);
  return (
    <PanelSectionRow>
      <Focusable style={{ display: "flex", gap: "8px" }}>
        <DialogButton
          style={{ minWidth: 0, flex: 1 }}
          onClick={() => backend.playAlbums(keys, false).then(onPlayed, () => {})}
        >
          <FaPlay /> &nbsp;Play all
        </DialogButton>
        <DialogButton
          style={{ minWidth: 0, flex: 1 }}
          onClick={() => backend.playAlbums(keys, true).then(onPlayed, () => {})}
        >
          <FaRandom /> &nbsp;Shuffle all
        </DialogButton>
      </Focusable>
    </PanelSectionRow>
  );
}

/** Album row: tap = play now (jumps back to Now Playing), ♥ = favorite,
 *  ＋ = append to the session without interrupting playback. */
function AlbumRow({
  album,
  isFav,
  onToggleFav,
  onPlayed,
}: {
  album: Album;
  isFav: boolean;
  onToggleFav: (key: string) => void;
  onPlayed: () => void;
}) {
  const sub = [album.albumArtist, album.year].filter(Boolean).join(" · ");
  return (
    <PanelSectionRow>
      <Focusable style={{ display: "flex", gap: "6px", alignItems: "stretch", padding: "2px 0" }}>
        <DialogButton
          style={{
            minWidth: 0,
            flex: 1,
            textAlign: "left",
            padding: "8px 8px",
            display: "flex",
            gap: "8px",
            alignItems: "center",
          }}
          onClick={() => backend.playAlbum(album.key, 0).then(onPlayed, () => {})}
        >
          <AlbumThumb albumKey={album.key} />
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: "13px", lineHeight: "17px", ...ELLIPSIS }}>{album.title}</div>
            <div style={{ fontSize: "11px", lineHeight: "14px", opacity: 0.6, ...ELLIPSIS }}>
              {sub || " "}
            </div>
          </div>
        </DialogButton>
        <DialogButton
          style={{ minWidth: "34px", flex: "0 0 34px", padding: 0 }}
          onClick={() => onToggleFav(album.key)}
        >
          {isFav ? <FaHeart style={{ color: ACCENT }} /> : <FaRegHeart style={{ opacity: 0.6 }} />}
        </DialogButton>
        <DialogButton
          style={{ minWidth: "34px", flex: "0 0 34px", padding: 0 }}
          onClick={() => void backend.enqueueAlbum(album.key)}
        >
          ＋
        </DialogButton>
      </Focusable>
    </PanelSectionRow>
  );
}

function AlbumLevel({
  title,
  albums,
  favKeys,
  onToggleFav,
  onPlayed,
  onBack,
}: {
  title: string;
  albums: Album[];
  favKeys: Set<string>;
  onToggleFav: (key: string) => void;
  onPlayed: () => void;
  onBack: () => void;
}) {
  return (
    <PanelSection>
      <LevelHeader title={title} onBack={onBack} />
      <SessionPills albums={albums} onPlayed={onPlayed} />
      {albums.map((a) => (
        <AlbumRow
          key={a.key}
          album={a}
          isFav={favKeys.has(a.key)}
          onToggleFav={onToggleFav}
          onPlayed={onPlayed}
        />
      ))}
    </PanelSection>
  );
}

/** Same row type as every other album list (thumb + heart + enqueue). */
function RecentlyAdded({
  recent,
  favKeys,
  onToggleFav,
  onPlayed,
}: {
  recent: Album[];
  favKeys: Set<string>;
  onToggleFav: (key: string) => void;
  onPlayed: () => void;
}) {
  return (
    <PanelSection title="Recently Added">
      {recent.length > 0 ? (
        recent.map((a) => (
          <AlbumRow
            key={a.key}
            album={a}
            isFav={favKeys.has(a.key)}
            onToggleFav={onToggleFav}
            onPlayed={onPlayed}
          />
        ))
      ) : (
        <PanelSectionRow>
          <Field description="Put album folders in ~/Music (or <card>/Music on an SD card / USB drive), then Settings → Rescan library." />
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

function Content() {
  const { status, elapsed } = useNowPlaying();
  return <BrowseShell status={status} elapsed={elapsed} />;
}

const REPLAY_GAIN_OPTIONS: { data: ReplayGainMode; label: string }[] = [
  { data: "off", label: "Off" },
  { data: "auto", label: "Auto" },
  { data: "album", label: "Album" },
  { data: "track", label: "Track" },
];

/** Settings: the two fixed READ-ONLY roots (internal ~/Music and <card>/Music)
 * plus Rescan (with live scan progress) and loudness normalization. No copying,
 * no path input — load the card from a PC, insert, rescan. */
function SettingsLevel({ status, onBack }: { status: PlayerStatus | null; onBack: () => void }) {
  const [roots, setRoots] = useState<backend.LibraryRoots | null>(null);
  const [busy, setBusy] = useState(false);
  const [scan, setScan] = useState<backend.ScanProgress | null>(null);

  useEffect(() => {
    backend.libraryRoots().then(setRoots).catch(() => {});
    const onScan = (p: backend.ScanProgress) => setScan(p);
    addEventListener<[backend.ScanProgress]>(backend.SCAN_EVENT, onScan);
    return () => removeEventListener(backend.SCAN_EVENT, onScan);
  }, []);

  const rescan = () => {
    setBusy(true);
    backend
      .rescanLibrary()
      .then(setRoots)
      .catch(() => {})
      .finally(() => setBusy(false));
  };

  const scanning = scan?.scanning === true;
  const rescanLabel = scanning
    ? `Scanning… ${scan!.songs} tracks`
    : busy
      ? "Rescanning…"
      : scan
        ? `Rescan library (${scan.songs} tracks)`
        : "Rescan library";

  return (
    <PanelSection>
      <LevelHeader title="Settings" onBack={onBack} />
      <PanelSectionRow>
        <Field label="Internal" description={roots?.internal ?? "…"} bottomSeparator="none" />
      </PanelSectionRow>
      {(roots?.externals ?? []).map((r) => (
        <PanelSectionRow key={r.name}>
          <Field
            label={`SD/USB: ${r.name.replace(/^sd-/, "")}`}
            description={`${r.path}${r.present ? "" : " — not present"}`}
            bottomSeparator="none"
          />
        </PanelSectionRow>
      ))}
      {roots && roots.externals.length === 0 && (
        <PanelSectionRow>
          <Field description="No SD card / USB drive with a Music folder detected. Put albums in <card>/Music, insert, then Rescan." />
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy || scanning} onClick={rescan}>
          {rescanLabel}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem
          label="Loudness normalization"
          description="ReplayGain — evens out volume between albums/tracks"
          rgOptions={REPLAY_GAIN_OPTIONS}
          selectedOption={status?.replayGainMode ?? "off"}
          onChange={(opt) => void backend.setReplayGain(opt.data as ReplayGainMode)}
        />
      </PanelSectionRow>
    </PanelSection>
  );
}

/** Root layout: the full NowPlaying stays pinned at the top in EVERY view —
 * only the browse pane below it switches. Recently Added shows at root only
 * (rendered inside BrowseImpl, which owns the album/favorite state). */
function BrowseShell({ status, elapsed }: { status: PlayerStatus | null; elapsed: number }) {
  const [view, setView] = useState<View>({ kind: "root" });
  return (
    <>
      <NowPlaying status={status} elapsed={elapsed} />
      <BrowseImpl status={status} view={view} setView={setView} />
    </>
  );
}

function BrowseImpl({
  status,
  view,
  setView,
}: {
  status: PlayerStatus | null;
  view: View;
  setView: (v: View) => void;
}) {
  const [albums, setAlbums] = useState<Album[]>([]);
  const [favorites, setFavorites] = useState<Album[]>([]);
  const [recent, setRecent] = useState<Album[]>([]);

  useEffect(() => {
    const refresh = () => {
      backend.allAlbums().then(setAlbums).catch(() => {});
      backend.favoriteAlbums().then(setFavorites).catch(() => {});
      backend.recentAlbums(8).then(setRecent).catch(() => {});
    };
    refresh();
    const onEvent = () => refresh();
    addEventListener(backend.PLAYER_EVENT, onEvent);
    return () => removeEventListener(backend.PLAYER_EVENT, onEvent);
  }, []);

  const artists = useMemo(() => {
    const counts = new Map<string, number>();
    for (const a of albums) counts.set(a.albumArtist, (counts.get(a.albumArtist) ?? 0) + 1);
    return [...counts.entries()].sort((x, y) => x[0].localeCompare(y[0]));
  }, [albums]);
  const soundtracks = useMemo(
    () => albums.filter((a) => isOST(a.genre)).sort((x, y) => x.title.localeCompare(y.title)),
    [albums],
  );
  const favKeys = useMemo(() => new Set(favorites.map((a) => a.key)), [favorites]);

  const goRoot = () => setView({ kind: "root" });
  const toggleFav = (key: string) => {
    // The sticker idle event also re-fetches; this direct refresh just makes
    // the heart respond instantly.
    backend
      .toggleFavorite(key)
      .then(() => backend.favoriteAlbums().then(setFavorites))
      .catch(() => {});
  };
  const albumLevelProps = { favKeys, onToggleFav: toggleFav, onPlayed: goRoot };

  if (view.kind === "artists") {
    return (
      <PanelSection>
        <LevelHeader title="Artists" onBack={goRoot} />
        <SessionPills albums={albums} onPlayed={goRoot} />
        {artists.map(([name, count]) => (
          <PanelSectionRow key={name}>
            <ButtonItem layout="below" onClick={() => setView({ kind: "artist", artist: name })}>
              {name}
              <span style={{ opacity: 0.6, fontSize: "0.8em" }}> &nbsp;{count} ›</span>
            </ButtonItem>
          </PanelSectionRow>
        ))}
      </PanelSection>
    );
  }

  if (view.kind === "artist") {
    const set = albums.filter((a) => a.albumArtist === view.artist);
    return (
      <AlbumLevel
        title={view.artist}
        albums={set}
        onBack={() => setView({ kind: "artists" })}
        {...albumLevelProps}
      />
    );
  }

  if (view.kind === "soundtracks") {
    return (
      <AlbumLevel title="Soundtracks" albums={soundtracks} onBack={goRoot} {...albumLevelProps} />
    );
  }

  if (view.kind === "favorites") {
    return (
      <AlbumLevel title="Favorites" albums={favorites} onBack={goRoot} {...albumLevelProps} />
    );
  }

  if (view.kind === "settings") {
    return <SettingsLevel status={status} onBack={goRoot} />;
  }

  // root
  return (
    <>
      <PanelSection title="Browse">
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setView({ kind: "artists" })}>
            Artists <span style={{ opacity: 0.6 }}>›</span>
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setView({ kind: "soundtracks" })}>
            Soundtracks <span style={{ opacity: 0.6 }}>›</span>
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setView({ kind: "favorites" })}>
            Favorites <span style={{ opacity: 0.6 }}>›</span>
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setView({ kind: "settings" })}>
            Settings <span style={{ opacity: 0.6 }}>›</span>
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      <RecentlyAdded
        recent={recent}
        favKeys={favKeys}
        onToggleFav={toggleFav}
        onPlayed={goRoot}
      />
    </>
  );
}

export default definePlugin(() => ({
  name: "WaveSafe",
  titleView: <div className={staticClasses.Title}>WaveSafe</div>,
  content: <Content />,
  icon: <FaMusic />,
}));
