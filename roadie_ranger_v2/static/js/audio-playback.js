// audio-playback.js
// Decodes base64 PCM-16 24 kHz chunks and plays them in order via Web Audio API.

export class AudioPlayback {
  constructor() {
    this._context    = null;
    this._nextStart  = 0;       // scheduled time for next buffer
    this._activeSrcs = [];      // all live BufferSource nodes, for barge-in flush
  }

  async init() {
    this._context    = new AudioContext({ sampleRate: 24000 });
    this._nextStart  = this._context.currentTime;
    this._activeSrcs = [];
  }

  /**
   * Enqueue a base64-encoded PCM-16 mono 24 kHz chunk for seamless playback.
   * @param {string} base64
   */
  enqueue(base64) {
    if (!this._context) return;

    // Decode base64 → Int16Array
    const binary  = atob(base64);
    const bytes   = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const int16   = new Int16Array(bytes.buffer);

    // Convert Int16 → Float32
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / (int16[i] < 0 ? 0x8000 : 0x7fff);
    }

    // Schedule buffer
    const buf = this._context.createBuffer(1, float32.length, 24000);
    buf.copyToChannel(float32, 0);

    const src = this._context.createBufferSource();
    src.buffer = buf;
    src.connect(this._context.destination);

    const now   = this._context.currentTime;
    const start = Math.max(this._nextStart, now);
    src.start(start);
    this._nextStart = start + buf.duration;

    // Track the node and remove it once it finishes naturally
    this._activeSrcs.push(src);
    src.onended = () => {
      const idx = this._activeSrcs.indexOf(src);
      if (idx !== -1) this._activeSrcs.splice(idx, 1);
    };
  }

  /**
   * Stop and discard all queued/playing audio immediately (barge-in).
   * Stops every scheduled BufferSource so the speaker goes silent at once.
   */
  flush() {
    if (!this._context) return;
    for (const src of this._activeSrcs) {
      try { src.stop(); } catch (_) { /* already ended */ }
    }
    this._activeSrcs = [];
    this._nextStart  = this._context.currentTime;
  }

  close() {
    this._context?.close();
    this._context    = null;
    this._nextStart  = 0;
    this._activeSrcs = [];
  }
}