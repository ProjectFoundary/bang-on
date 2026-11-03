/** Native source time mapping. Frame editing stays index-based; playback uses PTS. */
export function safeFps(fps = 24): number {
  return Number.isFinite(fps) && fps > 0 ? fps : 24;
}

export function safeAspect(aspectRatio = 16 / 9): number {
  return Number.isFinite(aspectRatio) && aspectRatio > 0 ? aspectRatio : 16 / 9;
}

export function frameTime(frame: number, fps = 24, timestamps?: readonly number[]): number {
  const index = Math.round(Number.isFinite(frame) ? frame : 0);
  if (timestamps?.length && index >= 0) {
    const first = timestamps[0];
    if (index < timestamps.length && Number.isFinite(timestamps[index]) && Number.isFinite(first))
      return Math.max(0, timestamps[index] - first);
    const last = timestamps[timestamps.length - 1];
    if (Number.isFinite(last) && Number.isFinite(first))
      return Math.max(0, last - first + (index - timestamps.length + 1) / safeFps(fps));
  }
  return index / safeFps(fps);
}

/** Last frame whose presentation timestamp has been reached, never24fps normalization. */
export function frameAtTime(seconds: number, frames: number, fps = 24, timestamps?: readonly number[]): number {
  if (frames <= 1 || !Number.isFinite(seconds) || seconds <= 0) return 0;
  const count = Math.max(1, Math.floor(frames));
  if (timestamps && timestamps.length >= count && Number.isFinite(timestamps[0])) {
    const target = seconds + timestamps[0] + .00001; // tolerate browser timestamp rounding
    let lo = 0, hi = count;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (timestamps[mid] <= target) lo = mid + 1;
      else hi = mid;
    }
    return Math.max(0, Math.min(count - 1, lo - 1));
  }
  return Math.max(0, Math.min(count - 1, Math.floor(seconds * safeFps(fps) + .00001)));
}

export const frameSecondsLabel = (frame: number, fps = 24, timestamps?: readonly number[]) =>
  frameTime(frame, fps, timestamps).toFixed(2) + 's';

/** Crop only our known bottom/right even-padding from browser previews. */
export function previewCrop(encodedWidth: number, encodedHeight: number,
                            pictureWidth?: number, pictureHeight?: number): [number, number] {
  if (!pictureWidth || !pictureHeight || pictureWidth <= 0 || pictureHeight <= 0) return [1, 1];
  if (encodedWidth === pictureWidth + pictureWidth % 2 && encodedHeight === pictureHeight + pictureHeight % 2)
    return [encodedWidth / pictureWidth, encodedHeight / pictureHeight];
  return [1, 1]; // legacy or differently sized video: do not infer a crop
}
