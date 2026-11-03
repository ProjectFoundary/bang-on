// Temporary blocking sprites only. These never enter the H3 guides or the final splice.
import type { Flash } from './api';
import { api } from './api';
import { image } from './effects';

export function renderFlashPreview(g: CanvasRenderingContext2D, flashes: Flash[], frame: number, redraw: () => void) {
  // Preload even on clean frames so the first gunshot is ready during playback.
  const sprite = image(api.stampUrl('muzzle_side', 'flash.png'), redraw);
  const w = g.canvas.width, h = g.canvas.height;
  for (const f of flashes) {
    if (f.frame !== frame) continue;
    const x = f.origin.x * w, y = f.origin.y * h;
    const dx = (f.tip.x - f.origin.x) * w, dy = (f.tip.y - f.origin.y) * h;
    const length = Math.hypot(dx, dy);
    if (length < 1) continue;
    g.save();
    g.translate(x, y); g.rotate(Math.atan2(dy, dx));
    const radius = Math.max(3, length * 0.65);
    const glow = g.createRadialGradient(length * 0.2, 0, 0, length * 0.2, 0, radius);
    glow.addColorStop(0, 'rgba(255,225,160,.5)');
    glow.addColorStop(0.3, 'rgba(255,170,70,.2)');
    glow.addColorStop(1, 'rgba(255,130,40,0)');
    g.globalCompositeOperation = 'lighter';
    g.fillStyle = glow; g.fillRect(length * 0.2 - radius, -radius, radius * 2, radius * 2);
    g.globalCompositeOperation = 'source-over';
    if (sprite.complete && sprite.naturalWidth) {
      // The existing side-on stamp's muzzle anchor is at 3% of its width.
      const width = length / 0.97, height = width * sprite.naturalHeight / sprite.naturalWidth;
      g.drawImage(sprite, -width * 0.03, -height * 0.5, width, height);
    }
    g.restore();
  }
}
