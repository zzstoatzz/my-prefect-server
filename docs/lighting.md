# Lighting

Prefect runs scheduled wind-downs and ad hoc lighting changes through
`lighting/home` on HeavyPad. For ad hoc changes, `changes` is an ordered
list. Each entry names a `target` (`all`, a room name, or a light name) and
exactly one of:

- `scene`: a saved Hue scene in the target room.
- `state`: any of `on`, `brightness` (percent), `temperature_kelvin`, `xy`,
  `effect`, `effect_speed`, `transition_seconds`.

```yaml
changes:
  - target: all
    state: {"on": false}
  - target: living room
    scene: Sahara
  - target: office
    state: {"on": true, "brightness": 30, "temperature_kelvin": 2700}
```

The whole list is resolved against the bridge inventory before any write, so a
misspelled room, light or scene fails the run without touching a bulb. A change
to `all` or a room gives each bulb the fields it supports; a change to one named
light fails if that bulb cannot honor it. After writing, the flow reads the
bridge back and fails unless every touched light matches.

Runs take the `home-presence-writer` lock that [presence](presence-lighting.md)
uses, so the two never interleave. This flow does not read or write the presence
marker.

## Scheduled changes

The `lighting/home` deployment owns the recurring fade in
`prefect.heavypad.yaml`. HeavyPad re-registers this manifest from main, so make
durable schedule changes there.

| Slug | When | Changes |
| --- | --- | --- |
| `wind-down` | 00:40–01:00 America/Chicago, Monday through Friday | fade lit, reachable lights to 5% |

Off lights and lights already at or below the target stay as they are. The
flow gives Hue one brightness transition per eligible light and logs
unreachable bulbs and unconfirmed commands by name. It does no follow-up
writes, so lights can be adjusted during the fade. Runs more than five minutes
late skip the fade. A run starting within that window still targets the
scheduled end time.

## One-night overrides

Schedule a replacement before either fade would start. The date is the local
calendar date of the fade, so after midnight use the new day's date. The
default duration is 20 minutes and `--minutes` accepts 15 through 30.

```sh
just prefect --script scripts/lighting_override.py 2026-11-25T02:00 --minutes 20
```

The latest override registered for that date wins. It can also schedule a fade
on a weekend morning. The override skips the regular run for that date and
has no effect on subsequent nights. Rescheduling after a fade has started
does not cancel the transition Hue already received.

## Ad hoc changes

```sh
just prefect deployment run lighting/home \
  --param 'changes=[{"target": "bedroom", "state": {"on": false}}]' --watch
```

## Code

- `flows/lighting.py`: the flow, lock and retries.
- `packages/mps/src/mps/home_lighting.py`: `LightChange`, `plan_changes`, and
  the readback contract.
- `packages/mps/src/mps/lighting.py`: smart-home MCP execution shared with
  presence.
