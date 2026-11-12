"""``mi ai …`` – KI-Provider anzeigen, wählen, testen; Schlüssel sicher hinterlegen und entfernen.

Schlüssel werden nie als Kommandozeilenargument angenommen (sie stünden sonst in der
Prozessliste und im Verlauf der Shell), sondern verdeckt abgefragt oder von stdin gelesen.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from .llm import manager
from .llm.nova_setup import NovaSetupError, create_access, find_cli, start_service
from .llm.secrets import SecretStoreError, default_store


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    ai = sub.add_parser("ai", help="KI-Provider: Status, Auswahl, Schlüssel, Verbindungstest")
    ai_sub = ai.add_subparsers(dest="ai_action", required=True)
    ai_sub.add_parser("status", help="aktiver Provider, Grund, Schlüssel (maskiert)")
    use = ai_sub.add_parser("use", help="Provider wählen (nova = Standard)")
    use.add_argument("provider", choices=sorted(manager.PROVIDER_INFO))
    key = ai_sub.add_parser("set-key", help="API-Key sicher hinterlegen (verdeckte Eingabe)")
    key.add_argument("provider", choices=["nova", "anthropic", "openai"])
    key.add_argument("--stdin", action="store_true", help="Schlüssel von stdin lesen")
    rm = ai_sub.add_parser("remove-key", help="API-Key aus dem sicheren Speicher löschen")
    rm.add_argument("provider", choices=["nova", "anthropic", "openai"])
    test = ai_sub.add_parser("test", help="Verbindung testen")
    test.add_argument("provider", nargs="?", choices=sorted(manager.PROVIDER_INFO))
    fb = ai_sub.add_parser("fallback", help="Fallback-Regel")
    fb.add_argument("rule", choices=sorted(manager.FALLBACK_RULES))
    model = ai_sub.add_parser("model", help="Modell eines Providers festlegen")
    model.add_argument("provider", choices=sorted(manager.PROVIDER_INFO))
    model.add_argument("model")
    ai_sub.add_parser("nova-setup", help="NOVA starten und Zugang automatisch einrichten")
    ai_sub.add_parser("log", help="letzte KI-Aufrufe (ohne Inhalte)")
    ai.set_defaults(func=cmd_ai)


def cmd_ai(args: argparse.Namespace) -> int:
    from .cli import _setup

    settings, engine, _ = _setup(args)
    store = default_store()
    action = args.ai_action
    try:
        if action == "use":
            manager.set_setting(engine, "provider", args.provider, "cli")
            manager.forget_probe()
        elif action == "set-key":
            value = (
                sys.stdin.readline().strip()
                if args.stdin
                else getpass.getpass(f"{manager.PROVIDER_INFO[args.provider]['key_label']}: ")
            )
            store.set(args.provider, value)
            manager.forget_probe(args.provider)
            print(f"gespeichert in: {store.label}")
        elif action == "remove-key":
            removed = store.delete(args.provider)
            manager.forget_probe(args.provider)
            print("gelöscht" if removed else "kein Schlüssel hinterlegt")
        elif action == "fallback":
            manager.set_setting(engine, "fallback", args.rule, "cli")
        elif action == "model":
            manager.set_setting(engine, f"model.{args.provider}", args.model, "cli")
        elif action == "test":
            name = args.provider or manager.resolve(engine, store, settings.llm).provider
            result = manager.probe(engine, name, store, settings.llm)
            print(f"{manager.PROVIDER_INFO[name]['label']}: {'OK' if result.ok else 'FEHLER'}")
            print(f"  {result.message}")
            if result.models:
                print(f"  Modelle: {', '.join(result.models[:12])}")
            return 0 if result.ok else 3
        elif action == "nova-setup":
            cli = find_cli()
            if cli is None:
                print("NOVA ist nicht installiert (nova.exe nicht gefunden).", file=sys.stderr)
                return 2
            start_service(cli)
            prefix = create_access(cli, store)
            manager.forget_probe("nova")
            print(f"NOVA-Zugang eingerichtet ({prefix}…), gespeichert in: {store.label}")
        elif action == "log":
            for row in manager.recent_calls(engine, 30):
                print(
                    f"{row['started_at']:%Y-%m-%d %H:%M:%S} {row['task']:18} "
                    f"{row['provider']}/{row['model'] or '-'} {row['status']}"
                    + (f" ({row['error_kind']}: {row['message']})" if row["error_kind"] else "")
                    + (f" [Fallback von {row['fallback_from']}]" if row["fallback_from"] else "")
                )
            return 0
    except (SecretStoreError, NovaSetupError, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2
    state = manager.status(engine, store, settings.llm)
    print(f"Aktiver Provider: {state['label']} (Modell: {state['model'] or 'nicht gewählt'})")
    print(f"  Grund: {state['selection'].reason}")
    print(f"  Fallback-Regel: {state['fallback']}")
    print(f"  Schlüsselspeicher: {state['store']}")
    for name, masked in state["keys"].items():
        print(f"  Schlüssel {name}: {masked or '—'}")
    return 0
