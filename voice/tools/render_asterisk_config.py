"""Render examples explicitly. Asterisk does not expand .env placeholders itself."""
import argparse
import os
import re
from pathlib import Path
from string import Template

from dotenv import dotenv_values


def render(source, target, env):
    files = {}
    for path in source.glob("*.conf.example"):
        text = path.read_text(encoding="utf-8")
        required = set(re.findall(r"\$\{([A-Z0-9_]+)\}", text))
        for key in required:
            value = env.get(key, "")
            if not value or any(char in value for char in "\r\n;[]"):
                raise ValueError(f"Set a nonempty, single-line, Asterisk-safe {key}")
        files[path.name.removesuffix(".example")] = Template(text).substitute(env)
    target.mkdir(parents=True, exist_ok=True)
    if any((target / name).exists() for name in files):
        raise FileExistsError("Generated configs already exist; choose a fresh --out directory")
    for name, text in files.items():
        path = target / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
    return list(files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=".env")
    parser.add_argument("--out", type=Path, default=Path("asterisk/generated"))
    args = parser.parse_args()
    names = render(Path(__file__).resolve().parents[1] / "asterisk", args.out,
                   dict(dotenv_values(args.env)) | dict(os.environ))
    print(f"Rendered {len(names)} files in {args.out}. Credentials are not printed.")
