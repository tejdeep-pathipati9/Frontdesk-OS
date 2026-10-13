/** Keep Daily/Vapi teardown serialized even when the call dialog is remounted. */
let teardown: Promise<void> = Promise.resolve();
const stopping = new WeakMap<object, Promise<void>>();
const starting = new WeakMap<object, Promise<unknown>>();

export function waitForVoiceTeardown() { return teardown; }

export function startVoiceClient<T>(client: object, start: () => Promise<T>) {
  const task = start();
  starting.set(client, task);
  void task.finally(() => starting.delete(client)).catch(() => {});
  return task;
}

export function stopVoiceClient(client: { stop: () => Promise<void> }) {
  const existing = stopping.get(client);
  if (existing) return existing;
  const task = teardown.then(async () => {
    // Closing a dialog while start() is awaiting microphone/network setup must
    // not destroy an empty client and then leave a late-created call running.
    await starting.get(client)?.catch(() => {});
    await client.stop();
  });
  stopping.set(client, task);
  teardown = task;
  // Keep a rejected teardown visible to the next start, without an unhandled
  // rejection if the dialog was closed while cleanup was running.
  void task.catch(() => {});
  return task;
}

/** A blocked speaker must not silently disconnect an otherwise healthy call. */
export class CallPlayback {
  private player: HTMLAudioElement | null = null;
  private owned = new Set<HTMLAudioElement>();
  private track: MediaStreamTrack | null = null;
  private disposed = false;
  constructor(private blocked: (value: boolean) => void) {}

  rememberTrack(track: MediaStreamTrack) { if (!this.disposed) this.track = track; }

  attach(player: HTMLAudioElement) {
    if (this.disposed) return;
    if (this.player && this.player !== player) {
      this.player.pause();
      this.player.srcObject = null;
      this.player.remove();
      this.owned.delete(this.player);
    }
    this.player = player;
    this.owned.add(player);
    player.muted = false;
    player.volume = 1;
    player.setAttribute("playsinline", "");
    void this.resume();
  }

  async resume() {
    // The SDK removes its element if autoplay fails before emitting `audio`.
    // Recover the public remote track under this explicit user gesture.
    if (!this.player && this.track && !this.disposed) {
      const player = document.createElement("audio");
      player.srcObject = new MediaStream([this.track]);
      player.setAttribute("playsinline", "");
      document.body.appendChild(player);
      this.player = player;
      this.owned.add(player);
    }
    const player = this.player;
    if (!player || this.disposed) { if (!this.disposed) this.blocked(true); return false; }
    try {
      // Called directly from the Enable audio click, preserving user activation.
      await player.play();
      if (!this.disposed && this.player === player) this.blocked(false);
      return true;
    } catch {
      if (!this.disposed && this.player === player) this.blocked(true);
      return false;
    }
  }

  dispose() {
    this.disposed = true;
    for (const player of this.owned) {
      player.pause();
      player.srcObject = null;
      player.remove();
    }
    this.owned.clear();
    this.player = null;
    this.track = null;
  }
}
