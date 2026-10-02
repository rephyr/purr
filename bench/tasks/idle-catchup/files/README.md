# pawtime

The idle core of a desktop pet game. The pet lives on your desktop; while the game is open, time
passes for it. These are the rules (the numbers live in `pawtime/data.json`).

## Care

- `food` and `mood` go from 0 to 100. While the game is open each one drains at its own rate
  (`drain_per_second`), down to the `floor`. Draining never takes a stat below the floor, and a
  stat that's already below it (an old save, a test) just stays where it is: draining never
  raises anything.
- `feed(amount)` adds to food and `play(amount)` adds to mood, up to 100.

## Buffs

- A buff is on while its stat is **strictly above** its line (`buffs[].above`). At exactly the
  line it's off.
- Every buff that's on multiplies coin income by its `x`. Buffs multiply with each other and with
  the pet's own `coin_boost` (its level bonus, 1.0 by default).

## Coins

- While the game is open the pet earns `income_per_second` × the multiplier, continuously.
- `pet.coins` is a whole number. What's left over (part of a coin) is kept in `pet.carry` and
  carries on: nothing is ever lost or rounded away.

## Errands

- `add_errand(id)` queues an errand (`errands` in data.json: how many `seconds` it takes, and its
  `reward`). The first errand in the queue runs while the game is open.
- When it has run for its `seconds` it's done: it pays `reward` × the multiplier **at that
  moment**, and the next errand in the queue starts at that very moment.

## Time

- `advance(seconds)` moves time on. The game calls it every frame with the frame time, and once
  with the whole gap when the computer wakes up from sleep. **Both must give exactly the same
  result:** advancing an hour in one call gives the same coins, carry, food, mood, errands and
  events as advancing it frame by frame, or in any other split.
- `close()` stops time for the pet (offline time doesn't count) until `open()`.
- `pet.clock` is how many seconds the game has been open in total.

## Events

`advance` returns what happened during it, in time order, as tuples:

- `("buff_off", buff_id, clock)`: a buff turned off at that moment
- `("errand_done", errand_id, clock)`: an errand finished at that moment

When several things happen at the same moment, buff changes come first, then errands.

## Saving

`save()` returns a dict that `json.dumps` can store, and `Pet.load(data)` gives back a pet that
carries on exactly as if it had never been saved.
