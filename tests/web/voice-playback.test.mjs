import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

let sequence = 0;
async function helpers() {
  const source = readFileSync(new URL("../../apps/web/lib/voice-playback.ts", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled + `\n// module ${++sequence}`).toString("base64")}`);
}
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const tick = () => new Promise(resolve => setImmediate(resolve));
function player(play = async () => {}) {
  return { muted: true, volume: 0, srcObject: {}, pauses: 0, removed: false, attributes: {}, play,
    pause() { this.pauses++; }, remove() { this.removed = true; }, setAttribute(key, value) { this.attributes[key] = value; } };
}

test("a second call waits for the first call's microphone and audio teardown", async () => {
  const { stopVoiceClient, waitForVoiceTeardown } = await helpers();
  const cleanup = deferred();
  let secondStarted = false, stops = 0;
  const first = { stop: async () => { stops++; await cleanup.promise; } };
  const ending = stopVoiceClient(first);
  assert.equal(stopVoiceClient(first), ending, "end event and End call must share cleanup");
  const next = (async () => { await waitForVoiceTeardown(); secondStarted = true; })();
  await tick();
  assert.equal(secondStarted, false);
  assert.equal(stops, 1);
  cleanup.resolve();
  await next;
  assert.equal(secondStarted, true);
});

test("closing during connection waits for a late-created SDK call, then destroys it", async () => {
  const { startVoiceClient, stopVoiceClient, waitForVoiceTeardown } = await helpers();
  const connection = deferred();
  let stopped = false;
  const client = { stop: async () => { stopped = true; } };
  const starting = startVoiceClient(client, () => connection.promise);
  const ending = stopVoiceClient(client);
  await tick();
  assert.equal(stopped, false);
  connection.resolve({ id: "new-call" });
  await starting;
  await ending;
  await waitForVoiceTeardown();
  assert.equal(stopped, true);
});

test("a teardown error blocks restart rather than overlapping a live old client", async () => {
  const { stopVoiceClient, waitForVoiceTeardown } = await helpers();
  await assert.rejects(stopVoiceClient({ stop: async () => { throw new Error("destroy failed"); } }), /destroy failed/);
  await assert.rejects(waitForVoiceTeardown(), /destroy failed/);
});

test("blocked playback can be resumed without restarting the call", async () => {
  const { CallPlayback } = await helpers();
  const blocked = []; let plays = 0;
  const audio = player(async () => { if (++plays === 1) throw new DOMException("gesture required", "NotAllowedError"); });
  const playback = new CallPlayback(value => blocked.push(value));
  playback.attach(audio);
  await tick();
  assert.deepEqual(blocked, [true]);
  assert.equal(await playback.resume(), true);
  assert.deepEqual(blocked, [true, false]);
  assert.equal(audio.muted, false);
  assert.equal(audio.volume, 1);
  playback.dispose();
  assert.equal(audio.srcObject, null);
  assert.equal(audio.removed, true);
});

test("an old call's late play rejection cannot affect the next session", async () => {
  const { CallPlayback } = await helpers();
  let reject;
  const delayed = new Promise((_, r) => { reject = r; });
  const blocked = [];
  const old = new CallPlayback(value => blocked.push(value));
  old.attach(player(() => delayed));
  old.dispose();
  const next = new CallPlayback(value => blocked.push(value));
  next.attach(player());
  await tick();
  reject(new Error("old audio ended"));
  await tick();
  assert.deepEqual(blocked, [false]);
  next.dispose();
});

test("SDK track replacement removes the old player so audio does not double", async () => {
  const { CallPlayback } = await helpers();
  const first = player(), next = player();
  const playback = new CallPlayback(() => {});
  playback.attach(first);
  playback.attach(next);
  await tick();
  assert.equal(first.removed, true);
  assert.equal(first.srcObject, null);
  assert.equal(next.removed, false);
  playback.dispose();
  assert.equal(next.removed, true);
});

test("Enable audio can recover the remote track when the SDK removed its blocked player", async () => {
  const { CallPlayback } = await helpers();
  const audio = player(), appended = [];
  const oldDocument = globalThis.document, oldStream = globalThis.MediaStream;
  globalThis.document = { createElement: () => audio, body: { appendChild: element => appended.push(element) } };
  globalThis.MediaStream = class { constructor(tracks) { this.tracks = tracks; } };
  try {
    const playback = new CallPlayback(() => {});
    const track = { kind: "audio" };
    playback.rememberTrack(track);
    assert.equal(await playback.resume(), true);
    assert.deepEqual(audio.srcObject.tracks, [track]);
    assert.deepEqual(appended, [audio]);
    playback.dispose();
    assert.equal(audio.removed, true);
  } finally {
    if (oldDocument === undefined) delete globalThis.document; else globalThis.document = oldDocument;
    if (oldStream === undefined) delete globalThis.MediaStream; else globalThis.MediaStream = oldStream;
  }
});
