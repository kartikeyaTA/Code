// waveform.js
// Animated bar-chart waveform that responds to microphone level.

const BAR_COUNT = 32;

export class WaveformViz {
  constructor(container) {
    this._container = container;
    this._bars      = [];
    this._level     = 0;
    this._raf       = null;
    this._phases    = Array.from({ length: BAR_COUNT }, (_, i) => (i / BAR_COUNT) * Math.PI * 2);
    this._build();
    this._animate();
  }

  _build() {
    this._container.innerHTML = '';
    for (let i = 0; i < BAR_COUNT; i++) {
      const bar = document.createElement('div');
      bar.className = 'bar';
      this._container.appendChild(bar);
      this._bars.push(bar);
    }
  }

  setLevel(level) {
    this._level = Math.max(0, Math.min(1, level));
  }

  _animate() {
    const t = performance.now() / 1000;
    const l = this._level;

    this._bars.forEach((bar, i) => {
      const wave  = Math.sin(t * 3 + this._phases[i]) * 0.5 + 0.5;  // 0-1
      const idle  = 2 + wave * 8;                                    // 2-10px idle
      const active = idle + l * 40 * wave;                           // scale with level
      bar.style.height = `${Math.round(active)}px`;
      bar.style.opacity = l > 0.01 ? `${0.4 + wave * 0.55}` : '0.2';
    });

    this._raf = requestAnimationFrame(() => this._animate());
  }

  stop() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    this._bars.forEach(b => { b.style.height = '4px'; b.style.opacity = '0.2'; });
  }
}