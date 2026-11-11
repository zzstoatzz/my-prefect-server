# Lighting

Every change to the home's lights that is not a presence reaction goes through
one flow, `lighting`, registered once as `lighting/home` on heavypad. The flow
takes `changes`, an ordered list. Each change names a `target` (`all`, a room
name, or a light name) and exactly one of:

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

A scheduled change is a schedule on the one deployment, carrying its own
`changes` as schedule parameters in `prefect.heavypad.yaml`. Adding a recurring
change means adding a schedule, not a deployment or a flow.

| Slug | When | Changes |
| --- | --- | --- |
| `nightly-off` | 01:00 America/Chicago, daily | every light off |

Schedules are written in local time with their `timezone`. The server honors
it since prefect-server `78beba2` (2026-11-11); before that it evaluated cron
in UTC and ignored the field.

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
