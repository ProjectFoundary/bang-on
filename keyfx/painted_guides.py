"""Local isolated-FX controls: paint, linear temporal blend, then sample cadence."""
from pathlib import Path

import cv2
import numpy as np

REVISION = 'paint-linear-blend-v1'
RIM_REVISION = 'directional-rim-after-blend-v1'
_CODES = np.arange(256, dtype=np.float32) / 255
_LINEAR = np.where(_CODES <= .04045, _CODES / 12.92, ((_CODES + .055) / 1.055) ** 2.4)


def recipe(settings):
    width = settings['frame_blend']
    result = {'revision': REVISION, 'style': settings['guide_style'], 'frame_blend': width,
            'blend_offsets': [-(width-1)//2 if width % 2 else -(width//2-1), width//2],
            'blend_space': 'linear BT709', 'endpoints': 'clamped', 'cadence': settings['cadence'],
            'guide_displacement': settings['guide_displacement'], 'guide_displacement_blur': settings['guide_displacement_blur'],
            'order': ['spatial blur', 'shading', 'paint', 'temporal blend', 'turbulent collider displacement', 'sparse sampling'],
            'light_rotation': settings['light_rotation'], 'light_elevation': settings['light_elevation'],
            'light_intensity': settings.get('light_intensity', 1), 'fill_intensity': settings.get('fill_intensity', 1)}
    if rim_enabled(settings):
        result.update(rim_revision=RIM_REVISION, rim_width=settings['rim_width'], rim_strength=settings['rim_strength'],
                      rim_width_reference=1024)
        result['order'] = ['spatial blur', 'shading', 'directional rim paint', 'temporal blend',
                           'turbulent collider displacement', 'final directional rim cap', 'sparse sampling']
    return result


def paint(image, settings, support=None, base_azimuth=120):
    source = image
    if settings['blur'] > 0:
        image = cv2.GaussianBlur(image, (0, 0), settings['blur'])
    if settings['shading'] != 1:
        image = np.round(255 * np.power(image.astype(np.float32) / 255, settings['shading'])).astype(np.uint8)
    if settings['guide_style'] == 'rendered':
        return image
    # Broad lit/shadow regions from the actual rendered light. Retain chroma
    # for fire guides; neutral smoke uses the tested 0/65/150/240 palette.
    value = image.max(axis=2)
    tone = np.array([0, 65, 150, 240], np.float32)[np.digitize(value, [16, 52, 84])]
    painted = np.clip(np.round(image.astype(np.float32) * (tone / np.maximum(value, 1))[..., None]), 0, 255).astype(np.uint8)
    if not rim_enabled(settings):
        return painted
    mask = source.max(axis=2) > 0 if support is None else np.asarray(support) > 0
    if mask.shape != image.shape[:2]:
        raise ValueError('Rim support differs from the guide canvas.')
    rim = rim_field(mask, settings, base_azimuth)
    shade = cv2.GaussianBlur(source, (0, 0), max(.1, 6*image.shape[1]/1024)).max(axis=2)
    strength = rim * (.75 + .25*np.clip(shade.astype(np.float32)/84, 0, 1))
    bands = np.where(mask, 1, 0)
    bands[(strength > .14) & mask] = 2
    bands[(strength > .48) & mask] = 3
    target = np.repeat(np.array([0,65,150,240],np.uint8)[bands][...,None],3,axis=2)
    return _mix(painted, target, settings['rim_strength'])


def rim_enabled(settings):
    return settings.get('guide_style') == 'painted' and settings.get('rim_strength', 0) > 0


def _encode(linear):
    return np.uint8(np.rint(255*np.clip(np.where(linear <= .0031308, 12.92*linear,
                        1.055*np.maximum(linear,0)**(1/2.4)-.055), 0, 1)))


def _mix(image, target, strength):
    if strength == 1:
        return target.copy()
    return _encode(_LINEAR[image]*(1-strength)+_LINEAR[target]*strength)


def rim_field(mask, settings, base_azimuth=120):
    """Inward contour band facing the projected camera-space light direction.

    Width and normal smoothing scale from the accepted 1024px-wide example.
    This guides painting only; simulated opacity and flow are never changed.
    """
    mask = np.asarray(mask, dtype=bool); scale = mask.shape[1]/1024
    distance = cv2.distanceTransform(np.pad(mask.astype(np.uint8),1),cv2.DIST_L2,cv2.DIST_MASK_PRECISE)[1:-1,1:-1]
    smooth = cv2.GaussianBlur(distance,(0,0),max(.1,2*scale))
    gy,gx = np.gradient(smooth)
    az = np.deg2rad(base_azimuth+settings.get('light_rotation',0)); el = np.deg2rad(settings.get('light_elevation',60))
    lx,ly = np.sin(az)*np.cos(el),np.sin(el); length = np.hypot(lx,ly)
    if length < 1e-6:
        facing = np.ones_like(distance) if np.cos(az)*np.cos(el) < 0 else np.zeros_like(distance)
    else:
        facing = np.clip((-gx*lx+gy*ly)/(np.maximum(np.hypot(gx,gy),1e-6)*length),0,1)
    width = max(.1,settings.get('rim_width',16)*scale)
    return np.float32(mask*np.exp(-.5*(distance/width)**2)*facing**1.25)


def finish(image, settings, base_azimuth=120):
    """Reapply the rim limit after temporal blending and turbulent displacement."""
    if not rim_enabled(settings):
        return image
    mask = image.max(axis=2) > 0; rim = rim_field(mask, settings, base_azimuth)
    t = np.clip((rim-.03)/(.40-.03),0,1); t = t*t*(3-2*t)
    maximum = _LINEAR[65]+(_LINEAR[240]-_LINEAR[65])*t
    target = _encode(np.minimum(_LINEAR[image],maximum[...,None]))
    target[~mask] = 0; target[mask] = np.maximum(target[mask],1)
    return _mix(image,target,settings['rim_strength'])


def lighting_prompt(settings, base_azimuth=120):
    if not rim_enabled(settings):
        return ''
    az = np.deg2rad(base_azimuth+settings.get('light_rotation',0))
    side = 'screen-left' if np.sin(az) < -.1 else 'screen-right' if np.sin(az) > .1 else 'silhouette'
    lateral = 'left' if np.sin(az) < -.1 else 'right' if np.sin(az) > .1 else ''
    placement = ('back-'+lateral if lateral else 'back') if np.cos(az) < -.1 else (lateral+'-side' if lateral else 'front')
    height = 'low ' if settings.get('light_elevation',60) <= 15 else 'high ' if settings.get('light_elevation',60) >= 60 else ''
    fill = 'very low fill' if settings.get('fill_intensity',1) <= .15 else 'the guided fill level'
    width = 'narrow' if settings.get('rim_width',16) <= 24 else 'broad'
    return (f' A single {height}{placement} key light grazes the smoke, tracing a {width} silver-white rim along the {side} contours and wisps. '
            f'The interior remains darker under {fill}, following the painted shadow regions. '
            'Lighting direction stays fixed throughout the shot. Render natural dimensional smoke and fine turbulent detail from the flat painted volume and lighting cues.')


def blend(directory: Path, count: int, width: int):
    left = (width - 1) // 2
    def read(index):
        image = cv2.imread(str(directory / f'{min(count-1, max(0, index)):05d}.png'))
        if image is None:
            raise ValueError('A painted guide frame is missing.')
        return image
    if width == 1:
        for frame in range(count):
            yield read(frame)
        return
    # Float64 accumulator avoids accumulating subtraction drift across a clip.
    total = sum((_LINEAR[read(k)].astype(np.float64) for k in range(-left, width-left)))
    for frame in range(count):
        linear = np.clip(total / width, 0, 1)
        encoded = np.where(linear <= .0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - .055)
        yield np.clip(np.round(encoded * 255), 0, 255).astype(np.uint8)
        if frame + 1 < count:
            total -= _LINEAR[read(frame-left)]
            total += _LINEAR[read(frame+width-left)]


class Displacement:
    """Seeded, smoothly evolving image warp around checked collider silhouettes.

    This is guide art direction; physical state and optical masks are untouched.
    All dimensions use logical960px units, like the live collision controls.
    """
    def __init__(self, root, project, effect, spec):
        self.root, self.project, self.spec = root, project, spec
        cfg = spec['settings']; self.amount = cfg['guide_displacement']
        self.falloff = cfg['guide_displacement_blur']
        self.mattes = [m for m in project.get('geometry', {}).get('mattes', [])
                       if m['id'] in cfg['colliders'] and m.get('role', 'both') != 'holdout']
        if not self.amount or not self.mattes:
            self.amount = 0
            return
        from . import post_holdouts
        self.matrices = post_holdouts.camera_matrices(project, spec['width'], spec['height'])
        w, h = spec['width'], spec['height']; self.scale = w / 960
        self.x, self.y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        rng = np.random.default_rng(cfg['seed'] % (2**32)); self.layers = []
        for wavelength, weight in [(96, 1), (48, .65), (24, .42), (12, .27), (6, .17)]:
            sigma = max(.8, wavelength * self.scale / 6)
            phases = []
            for _ in range(2):
                noise = cv2.GaussianBlur(rng.standard_normal((h, w)).astype(np.float32), (0, 0), sigma)
                dy, dx = np.gradient(noise); norm = max(1e-8, np.sqrt(np.mean(dx*dx + dy*dy)))
                phases.append((dy / norm, -dx / norm))
            self.layers.append((weight, phases))
        self.wake = np.zeros((h, w), np.float32)

    def apply(self, image, frame):
        if not self.amount:
            return image
        from . import post_holdouts
        h, w = image.shape[:2]; union = np.zeros((h, w), np.float32)
        source_frame = self.spec['source_frames'][frame]
        for matte in self.mattes:
            mask = post_holdouts.read_mask(self.root, matte, source_frame, original=True)
            mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
            if matte.get('static'):
                mask = cv2.warpPerspective(mask, self.matrices[source_frame], (w, h))
            radius = round(abs(matte.get('colliderDisplacement', 0)) * self.scale)
            if radius:
                kernel = np.ones((2*radius+1, 2*radius+1), np.uint8)
                mask = (cv2.dilate if matte['colliderDisplacement'] > 0 else cv2.erode)(mask, kernel)
            if matte.get('colliderBlur', 0):
                mask = cv2.GaussianBlur(mask, (0, 0), matte['colliderBlur'] * self.scale)
            union = np.maximum(union, mask)
        self.wake = np.maximum(union, self.wake * .85)
        if not self.wake.any():
            return image
        # Outside silhouette distance creates a soft envelope. Flow remains
        # coherent across frames; no fresh per-frame random seed or bulk bend.
        distance = cv2.distanceTransform(np.uint8(self.wake < .02), cv2.DIST_L2, 5)
        envelope = np.exp(-.5 * (distance / (self.falloff*self.scale))**2)
        shade = image.max(axis=2).astype(np.float32) / 255
        smooth = cv2.GaussianBlur(shade, (0, 0), max(1, 5*self.scale))
        dy, dx = np.gradient(smooth); edge = np.minimum(1, 12*np.hypot(dx, dy))
        support = envelope * edge
        vx, vy = np.zeros_like(union), np.zeros_like(union)
        phase = frame * .025
        mx, my = self.x-frame*2.2*self.scale, self.y+frame*.6*self.scale
        for weight, phases in self.layers:
            for value, factor in zip(phases, (np.cos(phase), np.sin(phase))):
                vx += weight*factor*cv2.remap(value[0], mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
                vy += weight*factor*cv2.remap(value[1], mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
        norm = max(1., float(np.sqrt(np.mean(vx*vx+vy*vy))))
        amplitude = self.amount*self.scale*support
        vx, vy = amplitude*np.tanh(vx/norm), amplitude*np.tanh(vy/norm)
        warped = cv2.remap(image, self.x-vx, self.y-vy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        warped[support < .004] = image[support < .004]
        return warped
