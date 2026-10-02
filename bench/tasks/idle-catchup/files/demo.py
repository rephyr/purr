"""Ten minutes with the pet, twice: played live (frame by frame) and caught up in one jump after
the computer wakes from sleep. They should come out exactly the same.

    python3 demo.py
"""

from pawtime import Pet

PINK, MINT, ROSE, DIM, BOLD, END = "\033[38;5;218m", "\033[38;5;151m", "\033[38;5;211m", "\033[2m", "\033[1m", "\033[0m"
MINUTES = 10
FPS = 60


def pet():
    p = Pet(food=70, mood=75)
    for errand in ("fetch_acorn", "chase_butterfly", "nap_in_sunbeam", "fetch_acorn"):
        p.add_errand(errand)
    return p


def clock(seconds):
    return f"{int(seconds) // 60}:{seconds % 60:05.2f}"


def main():
    live, slept = pet(), pet()
    live_events = []
    for _ in range(MINUTES * 60 * FPS):
        live_events += live.advance(1 / FPS)
    slept_events = slept.advance(MINUTES * 60)

    print(f"\n  {PINK} /\\_/\\ {END}  {BOLD}pawtime{END}: {MINUTES} minutes with your pet, twice")
    print(f"  {PINK}( •ω• ){END}  {DIM}live: {MINUTES * 60 * FPS} frames of 1/{FPS} s · after sleep: one jump{END}")
    print(f"  {PINK} > ^ < {END}\n")
    rows = [("coins", live.coins, slept.coins), ("carry", f"{live.carry:.4f}", f"{slept.carry:.4f}"),
            ("food", f"{live.food:.4f}", f"{slept.food:.4f}"), ("mood", f"{live.mood:.4f}", f"{slept.mood:.4f}"),
            ("errands left", len(live.errands), len(slept.errands))]
    print(f"  {DIM}{'':<14}{'live':>12}{'after sleep':>14}{END}")
    bad = 0
    for name, a, b in rows:
        ok = str(a) == str(b)
        bad += not ok
        print(f"  {name:<14}{str(a):>12}{str(b):>14}   {MINT + '✓' if ok else ROSE + '✗'}{END}")

    print(f"\n  {DIM}what happened{END}")
    for i in range(max(len(live_events), len(slept_events))):
        a = live_events[i] if i < len(live_events) else None
        b = slept_events[i] if i < len(slept_events) else None
        ok = a is not None and b is not None and a[:2] == b[:2] and abs(a[2] - b[2]) < 1e-6
        bad += not ok
        left = f"{clock(a[2])} {a[1]}" if a else "-"
        right = f"{clock(b[2])} {b[1]}" if b else "-"
        print(f"  {left:<26}{right:<26} {MINT + '✓' if ok else ROSE + '✗'}{END}")

    if bad:
        print(f"\n  {ROSE}✗ {bad} thing{'s' * (bad != 1)} don't match: catching up after sleep should be "
              f"exactly like playing live{END}\n")
    else:
        print(f"\n  {MINT}✓ everything matches ₊˚✧ the pet can't tell whether you slept{END}\n")


if __name__ == "__main__":
    main()
