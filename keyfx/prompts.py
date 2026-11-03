"""Structured MiniMax H3 prompts, assembled from the shot description and each effect's preset fragments."""
from __future__ import annotations

import math
import re

from . import keys, presets
from .config import FPS


_SOURCE_SURFACE = (r'(?:green[ -]?screen(?:\s+(?:floor|cloth|backdrop|stage))?|'
                   r'blue[ -]?screen(?:\s+(?:floor|cloth|backdrop|stage))?|'
                   r'(?:(?:green|blue)\s+)?crash\s+mats?|studio(?:\s+(?:floor|set|stage|equipment))?)')


def source_motion(text: str, *, clean_scene: bool = False) -> str:
    """Keep the reviewed action, omitting source-stage and camera assertions.

    The full video supplies camera motion. Production backgrounds in captions
    are evidence about the input, never instructions for the replacement scene.
    A clean scene pass also defers discharge/effects language to the FX pass.
    The user's saved description is not modified.
    """
    kept=[]
    for sentence in re.split(r'(?<=[.!?])\s+',text.strip()):
        if re.search(r'\bcamera\s+(?:movement|motion|stays|remains|is|moves|tracks|pans|tilts|follows)|^\s*(?:the\s+)?camera\b',sentence,re.I):
            continue
        if clean_scene and re.search(r'\b(?:fires?|fired|firing|shoots?|shooting|gunfire|muzzle\s+flash(?:es)?|tracers?|sparks?|explosions?)\b',sentence,re.I):
            continue
        sentence=re.sub(r'\b(?:on|against|in front of|inside|in|across|over|by)\s+(?:(?:a|an|the)\s+)?'
                        r'(?:(?:original|source)\s+)?'+_SOURCE_SURFACE+r'\b','',sentence,flags=re.I)
        if re.search(r'\b'+_SOURCE_SURFACE+r'\b',sentence,re.I):continue
        sentence=re.sub(r',?\s*\b(?:as if|as though|suggesting|seemingly|anticipating)\b[^.!?]*','',sentence,flags=re.I)
        sentence=re.sub(r'\s+([,.!?])',r'\1',re.sub(r'\s+',' ',sentence)).strip()
        if sentence and sentence.strip('.!? '):kept.append(sentence)
    return ' '.join(kept)


def secs(frame: int) -> str:
    return f'{frame / FPS:.2f} seconds'


def _fill(text: str, effect: dict) -> str:
    vals = {'where': effect.get('where') or 'the marked spot'}
    for k in sorted(effect['keys'], key=lambda k: k['frame'], reverse=True):     # first key of each phase wins
        vals[k['phase']] = secs(k['frame']) if k['frame'] >= 0 else 'before the shot begins'
    return text.format(**vals)


_WORDS = ['upward', 'up and toward the right of the frame', 'toward the right of the frame', 'down and toward the right of the frame',
          'downward', 'down and toward the left of the frame', 'toward the left of the frame', 'up and toward the left of the frame']


def direction_words(angle: float) -> str:
    """Screen direction for an angle in degrees (0 = up, clockwise)."""
    return _WORDS[int(((angle % 360) + 22.5) // 45) % 8]


def direction_phrase(effect: dict) -> str:
    """How the effect moves, from its aim handle: '' when there is nothing to say."""
    d = presets.get(effect['preset']).get('direction', {})
    kind = d.get('kind', 'none')
    s = float(effect.get('strength', d.get('strength', 0)))
    w = direction_words(float(effect.get('angle', d.get('angle', 0))))
    if kind == 'wind':
        if s < 0.15 or abs(math.sin(math.radians(float(effect.get('angle', d.get('angle', 0)))))) < 0.35:
            return ''                                   # no wind (or straight up/down): let the preset text describe the shape
        if s < 0.45:
            return f'drifting gently {w} in a light breeze'
        if s < 0.75:
            return f'blown {w} by the wind'
        return f'blown hard {w} by a strong wind, streaming in that direction'
    if kind == 'spray':
        return f'spraying {w}' if s >= 0.2 else ''
    if kind == 'barrel':
        return f'firing {w}'
    return ''


def strong_wind(effect: dict) -> bool:
    """Wind strong enough that 'rising column' wording would fight it."""
    d = presets.get(effect['preset']).get('direction', {})
    return d.get('kind') == 'wind' and float(effect.get('strength', d.get('strength', 0))) >= 0.45


def _direction_sentence(effect: dict) -> str:
    ph = direction_phrase(effect)
    if not ph:
        return ''
    noun = presets.get(effect['preset']).get('direction', {}).get('noun', 'effect')
    return f' The {noun} {"is " if not ph.startswith(("rising", "drifting", "spraying", "firing")) else "keeps "}{ph} throughout.'


def _variant(p: dict, base: str, pre: bool, windy: bool) -> str | None:
    """The preset's best text for this effect: pre-roll + wind, pre-roll, wind, then plain. None = pre-rolled with no pre-roll text."""
    order = ([f'{base}_preroll_wind'] if pre and windy else []) + ([f'{base}_preroll'] if pre else []) + ([f'{base}_wind'] if windy and not pre else [])
    for k in order:
        if p.get(k):
            return p[k]
    return None if pre else p[base]


def looks(effects: list[dict]) -> list[tuple[str, str, str]]:
    """Unique look references across the effects, in order: (preset id, file, description)."""
    seen, out = set(), []
    for e in effects:
        p = presets.get(e['preset'])
        for lk in p.get('looks', []):
            if (p['id'], lk['file']) not in seen:
                seen.add((p['id'], lk['file'])); out.append((p['id'], lk['file'], lk['text']))
    return out


def effect_prompt(shot: str, effects: list[dict], picture_offset: int = 0, guide_mode: str = 'full') -> str:
    shot=source_motion(shot)
    subjects, timings, actions, sounds = [], [], [], []
    for e in effects:
        p = presets.get(e['preset'])['prompt']
        ph, windy = direction_phrase(e), strong_wind(e)
        subjects.append(_fill(p.get('subject_wind') if windy and p.get('subject_wind') else p['subject'], e) + (f', {ph}' if ph else ''))
        pre = keys.prerolled(e)
        subj = _fill(p['subject'], e)
        before = next((k for k in e['keys'] if k['phase'] == 'before'), None)
        t = _variant(p, 'timing', pre, windy)
        t = _fill(t, e) if t else f'{subj} is already under way when the shot begins and continues throughout'
        if before and before['frame'] >= 0 and not pre:
            t = f'{e.get("where") or "the marked spot"} is untouched at {secs(before["frame"])}, then ' + t
        timings.append(t)
        a = _variant(p, 'action', pre, windy)
        actions.append((_fill(a, e) if a else f'From the very first frame {subj} is already under way and continues throughout.')
                       + _direction_sentence(e))
        if p.get('sound') and p['sound'] not in sounds:
            sounds.append(p['sound'])
    lines = ['subject_definitions:', '<Subject 1> is the people, objects and location from the source footage.']
    lines += [f'<Subject {i + 2}> is {s}.' for i, s in enumerate(subjects)]
    lines += ['', 'summary: [reference generation] A continuous real-time shot keeps the supplied footage and adds '
              + '; '.join(subjects) + '.', '', 'retention_analysis:']
    for i, (_, _, text) in enumerate(looks(effects)):
        lines.append(f'<Picture {picture_offset + i + 1}>: attribute_transfer. {text} It says nothing about the location or its '
                     'lighting, and it does not define the opening frame.')
    guide_scope = ('The explosion image guides establish the clean pre-blast moment, onset and peak only. '
                   'Continue its flame, smoke and debris motion naturally afterward, following the action timing below; '
                   'later timing descriptions are not additional image anchors. ' if guide_mode == 'sparse' else '')
    lines.append('The proxy video is the original footage: it controls the performance, the location, its lighting and the camera '
                 'motion. ' + guide_scope + 'The effect timing is: ' + '; '.join(timings) + '. The location keeps its own lighting '
                 'throughout; the only added light comes from the effects themselves. No shafts or beams of light appear.')
    lines += ['', 'detailed_description:', f'Live-action footage, real-time speed. [Shot 1] {shot.strip().rstrip(".") or "The original action plays out"}. '
              + ' '.join(actions) + ' Keep the original performance, the location and the original camera motion. One uninterrupted take.',
              '', f'overall_soundscape: {", ".join(sounds).capitalize() or "Ambient sound"}. No speech.', '', 'non_diegetic_music: N/A']
    return '\n'.join(lines)


def environment_prompt(shot: str, location: str, dressing: list[str] | None = None, lighting: str = '',
                       opening_guided: bool = False, opening_picture: int = 1,
                       extra_people_reference: bool = False) -> str:
    """Reference roles from the accepted smaller-scene test; action narration is deliberately omitted.

    `shot` remains accepted for callers/saved requests. Effects consume it separately.
    """
    loc = location.strip().rstrip('.') or 'the supplied location'
    identity = (f'only the selected people visible in <Picture {opening_picture}>' if opening_guided
                else 'the foreground people in <Video 1>')
    retention = (
        '<Video 1>: partially_preserved. Transfer the foreground people\'s identity, clothing, held props, exact performance, '
        'timing, screen positions and motion blur. Preserve its camera trajectory and framing progression.\n'
        '<Picture 1>: partially_preserved. Use its complete environment, ground, architecture, dressing, perspective and lighting throughout.')
    if opening_guided:
        retention += (f'\n<Picture {opening_picture}>: partially_preserved. Its selected people, placement and pose define the opening state.'
                      if opening_picture != 1 else ' Its people, placement and pose define the opening state.')
    if extra_people_reference:
        retention += ('\n<Video 2>: attribute_transfer. Additional isolated evidence of the same selected people\'s identities, '
                      'hair, clothing and body proportions at the same frame times and full-frame positions. '
                      'Black areas are empty matte; use <Picture 1> for the environment and lighting.')
    task = 'video editing + keyframe completion' if opening_guided else 'video editing + reference generation'
    opening = (f'Begin at <Picture {opening_picture}> at 0.00 seconds. ' if opening_guided else '')
    extra = (' The set includes ' + '; '.join(dressing) + '.') if dressing else ''
    if lighting:
        extra += f' Lighting: {lighting}.'
    return (f'subject_definitions:\n<Subject 1> is {identity}, with identity, clothing, body proportions and held props from <Video 1>.\n'
            f'<Subject 2> is the complete environment in <Picture 1>: {loc}.\n\n'
            f'summary: [{task}] Integrate <Subject 1> into <Subject 2>, using <Picture {opening_picture}> '
            'as the opening and <Video 1> as the performance and camera reference.\n\n'
            f'retention_analysis:\n{retention}\n\n'
            f'detailed_description:\nLive-action footage. [Shot 1] {opening}'
            'Follow <Video 1> for the entire performance, original speed, timing, camera movement and ending. '
            'The complete environment from <Picture 1> fills the foreground, ground and both side edges throughout the shot. '
            'Newly revealed areas extend this same environment coherently. Location illumination, reflected colour and natural '
            'contact shadows integrate the people and held props into the location. Preserve the environment\'s layout, dressing '
            f'and perspective through one continuous take.{extra}\n\n'
            'overall_soundscape: Quiet location ambience.\n\nnon_diegetic_music: N/A')
