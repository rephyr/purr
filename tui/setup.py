"""purr setup: the first start (or `purr setup`, /setup) in a little window. Hello, the local models
purr found, the API providers (a key checked and saved in one go, or your own OpenAI-compatible
server), the model to start on, and the cat. Nothing is saved until the end; then it's all in
~/.config/purr/config.toml (keys in keys.toml), where you can change anything later.
The work itself is harness/onboard.py; this only shows it.
"""

import os
import time

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (Button, ContentSwitcher, Input, OptionList, RadioButton, RadioSet, SelectionList,
                             Static)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from harness import onboard, settings, ui
from tui import cat
from tui.themes import DIM, FAINT, LILAC, MINT, PEACH, PINK, TEXT
from tui.window import BenchWindow

STEPS = [("hello", "hello"), ("local", "local models"), ("api", "API providers"), ("model", "your model"),
         ("cat", "your cat"), ("done", "all set")]
OWN = "__own__"  # the "your own provider" entry
THEMES = [("auto", "auto: bright by day, deep at night"), ("cotton-candy", "cotton candy (day)"),
          ("bubblegum-night", "bubblegum night"), ("plum", "plum"), ("strawberry-milk", "strawberry milk"),
          ("lilac-dream", "lilac dream")]

CSS = """
#setup { padding: 1 2; }
#title { width: 100%; text-align: center; }
#subtitle { width: 100%; text-align: center; color: $dim; margin-bottom: 1; }
#body { height: 1fr; }
#steps { width: 24; height: auto; padding: 1 1; border: round $line; background: $panel; margin-right: 2; }
#pages { width: 1fr; height: 1fr; border: round $line-hi; background: $panel; padding: 1 2; }
#pages > * { height: 1fr; }
.lead { margin-bottom: 1; }
.hint { color: $hint; margin-top: 1; }
#api-row, #own-row { height: 1fr; }
#providers { width: 34; height: 1fr; border: round $line; background: $bg; margin-right: 2; }
#providers:focus { border: round $pink; }
#provider-side { width: 1fr; height: 1fr; }
#provider-info { height: auto; margin-bottom: 1; }
Input { border: round $line; background: $bg; margin-bottom: 1; }
Input:focus { border: round $pink; }
#models, #own-models { height: 1fr; border: round $line; background: $bg; }
#models:focus, #own-models:focus { border: round $pink; }
#themes { border: round $line; background: $bg; width: 50; }
#themes:focus { border: round $pink; }
#nav { height: auto; margin-top: 1; align-horizontal: right; }
#nav Button, #pages Button { margin-left: 2; background: $button; color: $fg; border: none; min-width: 14; }
#nav Button:focus, #pages Button:focus { background: $pink; color: $ink; text-style: bold; }
#pages Button { margin-left: 0; margin-right: 2; }
.buttons { height: auto; }
#setupcat { height: 3; margin-top: 1; }
/* a small terminal (80x24): the steps list, subtitle and cat make room for the page */
.small #setup { padding: 0 1; }
.small #steps, .small #subtitle, .small #setupcat { display: none; }
.small #pages { padding: 0 1; }
.small #providers { width: 22; margin-right: 1; }
.small #nav { margin-top: 0; }
"""


class SetupApp(BenchWindow):
    TITLE = "purr setup"
    CSS_PATH = ["purr.tcss"]
    CSS = CSS
    BINDINGS = [Binding("ctrl+q", "quit_setup", "quit", priority=True),
                Binding("escape", "back", "back")]

    def __init__(self, config=None):
        super().__init__(config or settings.load(discover=False))
        self.step = 0
        self.found = {}
        self.extra = {}  # your own providers and models, saved at the end
        self.picked_model = None
        self.provider_choice = None
        self.own_found = []

    # ---- layout ----

    def compose(self) -> ComposeResult:
        with Vertical(id="setup"):
            yield Static(cat.shimmer("₊˚✧ welcome to purr ✧˚₊", 0.1), id="title")
            yield Static("a minute of setup, then you're coding · nothing here is final: /setup any time",
                         id="subtitle")
            with Horizontal(id="body"):
                yield Static("", id="steps")
                with ContentSwitcher(initial="hello", id="pages"):
                    with VerticalScroll(id="hello"):
                        yield Static(self.hello_text(), classes="lead")
                    with VerticalScroll(id="local"):
                        yield Static("looking around this machine…", id="local-text")
                        with Horizontal(classes="buttons"):
                            yield Button("make roomier copies", id="roomy")
                            yield Button("look again", id="rescan")
                    with Vertical(id="api"):
                        yield Static("", id="api-lead", classes="lead")
                        with Horizontal(id="api-row"):
                            yield OptionList(id="providers")
                            with VerticalScroll(id="provider-side"):
                                yield Static("", id="provider-info")
                                yield Input(placeholder="its address, like https://llm.example.com/v1", id="own-url")
                                yield Input(placeholder="a short name for it", id="own-name")
                                yield Input(placeholder="paste the key here (hidden)", password=True, id="key")
                                with Horizontal(classes="buttons"):
                                    yield Button("check & save", id="save-key")
                                    yield Button("find its models", id="own-find")
                                    yield Button("add these", id="own-add")
                                yield SelectionList(id="own-models")
                    with Vertical(id="model"):
                        yield Static("", id="model-lead", classes="lead")
                        yield OptionList(id="models")
                    with VerticalScroll(id="cat"):
                        yield Static("what's her name?", classes="lead")
                        yield Input(value=self.name_, placeholder="Mochi", id="cat-name")
                        yield Static("and purr's colours (dark, always):", classes="lead")
                        with RadioSet(id="themes"):
                            for key, label in THEMES:
                                yield RadioButton(label, value=key == self.config.get("theme", "auto"), name=key)
                    with VerticalScroll(id="done"):
                        yield Static("", id="done-text", classes="lead")
            with Horizontal(id="nav"):
                yield Button("back", id="back")
                yield Button("next ✧", id="next")
            yield Static("", id="setupcat")

    def on_resize(self, event):
        self.screen.set_class(event.size.width < 110 or event.size.height < 34, "small")

    def on_mount(self):
        self.set_interval(0.12, self.tick)
        self.mood("greeting", "", hold=4)
        self.show_step(0)
        self.query_one("#next", Button).focus()

    def hello_text(self):
        t = Text()
        t.append("hi! I'm purr ♡ a coding agent for your terminal\n\n", style=f"bold {PINK}")
        for line in ("I'll look for the local models you're running (Ollama, llama.cpp, LM Studio, vLLM)",
                     "you can add API providers with just a key, or your own server",
                     "then pick the model to start on, and name the cat"):
            t.append("  ✧ ", style=LILAC)
            t.append(line + "\n", style=TEXT)
        t.append("\nnothing gets saved until the end, and you can change all of it later.", style=DIM)
        return t

    # ---- moving between steps ----

    def show_step(self, i):
        self.step = max(0, min(len(STEPS) - 1, i))
        key = STEPS[self.step][0]
        self.query_one("#pages", ContentSwitcher).current = key
        steps = Text()
        for n, (_, label) in enumerate(STEPS):
            mark, style = ("✓ ", MINT) if n < self.step else ("♡ ", f"bold {PINK}") if n == self.step else ("◌ ", FAINT)
            steps.append(mark, style=style)
            steps.append(label + "\n", style=style if n == self.step else (TEXT if n < self.step else DIM))
        self.query_one("#steps", Static).update(steps)
        self.query_one("#back", Button).display = self.step > 0
        self.query_one("#next", Button).label = "start purr ♡" if key == "done" else "next ✧"
        getattr(self, f"enter_{key}", lambda: None)()

    def action_back(self):
        if self.step > 0:
            self.show_step(self.step - 1)

    def on_button_pressed(self, event):
        handler = {"next": self.next_step, "back": self.action_back, "roomy": self.make_roomy,
                   "rescan": self.enter_local, "save-key": self.save_key, "own-find": self.own_find,
                   "own-add": self.own_add}.get(event.button.id)
        if handler:
            handler()

    def next_step(self):
        key = STEPS[self.step][0]
        if key == "model" and not self.picked_model:
            self.notify("pick a model to start on ♡ (or add a provider first)", severity="warning")
            return
        if key == "done":
            self.save_and_exit()
            return
        self.show_step(self.step + 1)

    # ---- local models ----

    def enter_local(self):
        self.query_one("#local-text", Static).update(Text("looking around this machine…", style=DIM))
        self.query_one("#roomy", Button).display = False
        self.mood("exploring", "looking for local models", hold=2, label=f"{self.name_} is sniffing around")
        self.scan()

    @work(thread=True, exclusive=True, group="scan")
    def scan(self):
        config, found = onboard.scan()
        self.call_from_thread(self.scanned, config, found)

    def scanned(self, config, found):
        self.config = settings.merge(config, self.extra)
        self.found = found
        names = {"ollama": "Ollama", "llamacpp": "llama.cpp", "lmstudio": "LM Studio", "vllm": "vLLM"}
        t = Text()
        if not found:
            t.append("no local model server is running right now\n\n", style=f"bold {PEACH}")
            where = self.config["providers"]["ollama"]["base_url"] if os.environ.get("OLLAMA_HOST") else ""
            t.append((f"(nothing answered at {where})\n" if where else "")
                     + "that's fine: add an API provider next. For free local models, get Ollama\n"
                     "(ollama.com) and `ollama pull` a model with tool calling (Qwen, Gemma, gpt-oss...),\n"
                     "then press look again.", style=TEXT)
            self.mood("purring", "nothing local yet, the API is fine too", hold=3, label=f"{self.name_} looked everywhere")
        for provider, models in found.items():
            t.append(f"{names.get(provider, provider)} ", style=f"bold {PINK}")
            t.append(f"· {len(models)} model{'s' * (len(models) != 1)} that can use tools\n", style=LILAC)
            for name in sorted(models, key=lambda n: -self.config["models"][n].get("context", 0))[:14]:
                ctx = self.config["models"][name].get("context", 0)
                small = ctx < onboard.AGENT_CONTEXT
                t.append(f"  {'⚠' if small else '✓'} ", style=PEACH if small else MINT)
                t.append(f"{name[:34]:<35}", style=DIM if small else TEXT)
                t.append(f"{ctx // 1024}k{' · too small for an agent' if small else ''}\n", style=PEACH if small else DIM)
            if len(models) > 14:
                t.append(f"  … and {len(models) - 14} more\n", style=DIM)
            t.append("\n")
        small = onboard.too_small(self.config)
        if small:
            t.append(f"{len(small)} Ollama model{'s' * (len(small) != 1)} run with a small context (Ollama's default).\n"
                     "'make roomier copies' gives each a 32k copy (only settings, no download).", style=DIM)
        if found:
            self.mood("happy", f"{sum(len(m) for m in found.values())} local models that can use tools", hold=3,
                      label=f"{self.name_} found your models ♡")
        self.query_one("#roomy", Button).display = bool(small)
        self.query_one("#local-text", Static).update(t)

    def make_roomy(self):
        self.query_one("#roomy", Button).display = False
        self.mood("building", "making roomier copies", hold=10**9, label=f"{self.name_} is making room")
        self.roomy_in_background(onboard.too_small(self.config))

    @work(thread=True, exclusive=True, group="roomy")
    def roomy_in_background(self, names):
        made, failed = [], []
        for name in names:
            try:
                made.append(onboard.make_roomy(self.config, name))
            except (OSError, ValueError, KeyError):
                failed.append(name)
        self.call_from_thread(self.roomy_done, made, failed)

    def roomy_done(self, made, failed):
        self.notify(f"made {len(made)} roomier cop{'ies' if len(made) != 1 else 'y'} ♡"
                    + (f" ({len(failed)} didn't work)" if failed else ""), timeout=5)
        self.enter_local()

    # ---- API providers ----

    def enter_api(self):
        self.query_one("#api-lead", Static).update(Text.assemble(
            ("models from an API need its key. ", TEXT), ("Pick one, paste the key, and purr checks it.\n", DIM),
            ("free", MINT), (" ones need no card (rate-limited; some learn from free prompts).", DIM)))
        options = self.query_one("#providers", OptionList)
        options.clear_options()
        for name, p in onboard.api_providers(self.config):
            have = onboard.key_source(p)
            line = Text(no_wrap=True)
            line.append("✓ " if have else "  ", style=MINT)
            line.append(f"{name:<11}", style=f"bold {TEXT}" if have else TEXT)
            line.append("free" if p.get("free_tier") else "", style=MINT)
            options.add_option(Option(line, id=name))
        options.add_option(Option(Text("＋ your own (any OpenAI-compatible)", style=LILAC), id=OWN))
        options.highlighted = 0
        options.focus()

    def on_option_list_option_highlighted(self, event):
        if event.option_list.id == "providers":
            self.show_provider(event.option.id)
        elif event.option_list.id == "models":
            self.picked_model = event.option.id

    def show_provider(self, name):
        self.provider_choice = name
        own = name == OWN
        for wid in ("#own-url", "#own-name", "#own-find", "#own-add", "#own-models"):
            self.query_one(wid).display = own
        self.query_one("#own-add").display = own and bool(self.own_found)
        self.query_one("#own-models").display = own and bool(self.own_found)
        self.query_one("#save-key").display = not own
        info = Text()
        if own:
            info.append("your own provider\n", style=f"bold {PINK}")
            info.append("any server that speaks the OpenAI API: a company gateway, a cloud GPU running\n"
                        "vLLM, another provider purr doesn't know. The key is optional.", style=DIM)
        else:
            p = self.config["providers"][name]
            have = onboard.key_source(p)
            info.append(f"{name}\n", style=f"bold {PINK}")
            info.append(f"{p['base_url']}\n", style=DIM)
            if have:
                info.append(f"✓ purr has its key ({have}). Paste another to replace it.\n", style=MINT)
            page = p.get("key_page")
            if page:
                info.append("get a key: ", style=TEXT)
                info.append(page, style=f"underline {LILAC}")
        self.query_one("#provider-info", Static).update(info)
        self.query_one("#key", Input).value = ""

    def save_key(self):
        key = self.query_one("#key", Input).value.strip()
        if not key or self.provider_choice in (None, OWN):
            self.notify("paste the key first ♡", severity="warning")
            return
        self.mood("waiting", f"checking the {self.provider_choice} key", hold=10**9, label=f"{self.name_} is checking")
        self.check_in_background(self.provider_choice, key)

    @work(thread=True, exclusive=True, group="key")
    def check_in_background(self, name, key):
        provider = self.config["providers"][name]
        ok, said = onboard.check_key(provider, key)
        if ok:
            onboard.store_key(provider["api_key_env"], key)
        self.call_from_thread(self.key_checked, name, ok, said)

    def key_checked(self, name, ok, said):
        if ok:
            self.mood("celebrating", f"{name} works ♡", hold=3, label="key saved!")
            self.notify(f"{name}: {said} saved", timeout=4)
            self.enter_api()
        else:
            self.mood("sad", f"{name}: {said}", hold=4, label="that key didn't work")
            self.notify(f"{name}: {said}", severity="error", timeout=8)

    def own_find(self):
        url = self.query_one("#own-url", Input).value.strip()
        if not url.startswith(("http://", "https://")):
            self.notify("its address starts with http:// or https:// ♡", severity="warning")
            return
        self.mood("exploring", "asking it for its models", hold=10**9, label=f"{self.name_} is asking around")
        self.own_in_background(url, self.query_one("#key", Input).value.strip())

    @work(thread=True, exclusive=True, group="own")
    def own_in_background(self, url, key):
        try:
            models = onboard.list_models(url, key or None)
            error = None
        except Exception as e:  # noqa: BLE001 - any failure is shown, the setup goes on
            reason = getattr(e, "reason", e)
            models, error = [], ("nothing is listening there (is the server running?)"
                                 if isinstance(reason, (ConnectionRefusedError, TimeoutError)) else str(reason)[:120])
        self.call_from_thread(self.own_listed, models, error)

    def own_listed(self, models, error):
        self.own_found = models
        picker = self.query_one("#own-models", SelectionList)
        picker.clear_options()
        for model_id, ctx in models[:200]:
            picker.add_option(Selection(f"{model_id}" + (f"  ({ctx // 1024}k)" if ctx else ""), model_id, len(models) <= 3))
        self.query_one("#own-add").display = bool(models)
        picker.display = bool(models)
        if error or not models:
            self.mood("sad", error or "it has no models", hold=4, label="no models there")
            self.notify(f"couldn't list its models: {error or 'none there'}", severity="error", timeout=8)
        else:
            self.mood("happy", f"it has {len(models)} models: tick the ones you want", hold=4, label="found some!")

    def own_add(self):
        name = self.query_one("#own-name", Input).value.strip() or "mine"
        picked = set(self.query_one("#own-models", SelectionList).selected)
        models = [(m, c) for m, c in self.own_found if m in picked]
        if not models:
            self.notify("tick at least one model ♡", severity="warning")
            return
        url = self.query_one("#own-url", Input).value.strip()
        added = onboard.custom_provider(name, url, self.query_one("#key", Input).value.strip() or None, models)
        settings.merge(self.extra, added)
        settings.merge(self.config, added)
        self.mood("celebrating", f"added {name}: {len(models)} model{'s' * (len(models) != 1)}", hold=3,
                  label="provider added!")
        self.notify(f"added {name} ♡", timeout=4)
        self.own_found = []
        self.enter_api()

    # ---- the model to start on ----

    def enter_model(self):
        usable = onboard.usable_models(self.config)
        best = self.picked_model if any(n == self.picked_model for n, _ in usable) else onboard.recommend(self.config)
        lead = Text()
        if usable:
            lead.append("the model purr starts on ", style=TEXT)
            lead.append("(switch any time with /model)\n", style=DIM)
            lead.append("★ ", style=PINK)
            lead.append("is my pick: a local model when there's a roomy one (free, nothing leaves this computer)",
                        style=DIM)
        else:
            lead.append("no model can run yet: go back and add an API key, or start a local server\n", style=PEACH)
        self.query_one("#model-lead", Static).update(lead)
        options = self.query_one("#models", OptionList)
        options.clear_options()
        index = 0
        for i, (name, spec) in enumerate(usable):
            line = Text(no_wrap=True, overflow="ellipsis")
            line.append("★ " if name == best else "  ", style=PINK)
            line.append(f"{name[:34]:<35}", style=f"bold {TEXT}" if name == best else TEXT)
            kind = ui.cost_kind(spec)
            line.append(f"{kind:<6}", style=MINT if kind in ("local", "free") else PEACH)
            line.append(f" {spec.get('context', 0) // 1024}k · {spec['provider']}", style=DIM)
            options.add_option(Option(line, id=name))
            if name == best:
                index = i
        if usable:
            options.highlighted = index
            self.picked_model = usable[index][0]
        options.focus()

    def on_option_list_option_selected(self, event):
        if event.option_list.id == "models":
            self.picked_model = event.option.id
            self.next_step()

    # ---- the end ----

    def enter_cat(self):
        self.query_one("#cat-name", Input).focus()

    def enter_done(self):
        name = self.query_one("#cat-name", Input).value.strip() or "Mochi"
        t = Text()
        t.append("all set ♡\n\n", style=f"bold {PINK}")
        t.append("  model     ", style=LILAC)
        t.append(f"{self.picked_model}\n", style=TEXT)
        local = sum(len(m) for m in self.found.values())
        t.append("  local     ", style=LILAC)
        t.append(f"{local} model{'s' * (local != 1)} found, found again every start\n", style=TEXT)
        keys = [n for n, p in onboard.api_providers(self.config) if onboard.key_source(p)]
        t.append("  API       ", style=LILAC)
        t.append(f"{', '.join(keys) if keys else 'none yet (purr --key <provider> adds one)'}\n", style=TEXT)
        t.append("  cat       ", style=LILAC)
        t.append(f"{name}\n\n", style=TEXT)
        t.append(f"saved in {settings.USER_CONFIG} · keys in keys.toml (only you can read it)\n", style=DIM)
        t.append("in purr: / opens the commands, shift+tab switches mode, /setup brings this back", style=DIM)
        self.query_one("#done-text", Static).update(t)
        self.mood("celebrating", "ready when you are", hold=10**9, label=f"{name} is ready ♡")
        self.query_one("#next", Button).focus()  # enter starts purr, like enter moved on every step before

    def save_and_exit(self):
        theme = self.query_one("#themes", RadioSet).pressed_button
        name = self.query_one("#cat-name", Input).value.strip() or "Mochi"
        onboard.finish(self.picked_model, cat_name=name, theme=theme.name if theme else None, extra=self.extra)
        self.exit(self.picked_model)

    def action_quit_setup(self):
        self.exit(None)

    def on_input_submitted(self, event):
        if event.input.id == "key":
            self.own_find() if self.provider_choice == OWN else self.save_key()
        elif event.input.id == "cat-name":
            self.next_step()

    # ---- Mochi ----

    def tick(self):
        if not self.screen.query("#title"):  # quitting: the widgets are gone
            return
        self.cat_tick += 1
        if self.cat_until and time.monotonic() > self.cat_until:
            self.mood("watching", "", label=f"{self.name_} is helping you set up")
        self.query_one("#title", Static).update(cat.shimmer("₊˚✧ welcome to purr ✧˚₊", self.cat_tick * 0.03))
        self.query_one("#setupcat", Static).update(cat.render(
            self.cat_mood, self.cat_tick, self.cat_detail, (), label=self.cat_label, night=self.night))
