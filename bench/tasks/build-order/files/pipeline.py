from build_order import CycleError, build_order

TARGETS = {
    "game.pck": ["scenes", "atlas"],
    "scenes": ["atlas", "fonts"],
    "atlas": ["textures"],
    "textures": [],
    "fonts": [],
}


def main():
    try:
        for target in build_order(TARGETS):
            print("building", target)
    except CycleError as e:
        print("can't build:", e)


if __name__ == "__main__":
    main()
