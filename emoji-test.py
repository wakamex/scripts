# %%
import os
import pkg_resources
import sys
import subprocess
from typing import Tuple
import urllib.request

installed_packages = [i.key for i in pkg_resources.working_set]
if "uharfbuzz" not in installed_packages:
    print("Installing uharfbuzz")
    subprocess.run([sys.executable, "-m", "pip", "install", "uharfbuzz"])
import uharfbuzz as hb  # type: ignore

if "codecs" not in installed_packages:
    print("Installing codecs")
    import sys

    subprocess.run([sys.executable, "-m", "pip", "install", "codecs"])
import codecs


# %%
def is_emoji_supported_by_font(hb, emoji: str) -> Tuple[bool, Tuple[hb.GlyphInfo]]:
    # Create text buffer:
    buf = hb.Buffer.create()
    buf.add_str(emoji)
    buf.guess_segment_properties()

    # Shape text:
    features = {"kern": 1, "liga": 1}
    hb.shape(font, buf, features)

    # Check if the emoji is supported
    glyph_infos: Tuple[hb.GlyphInfo] = buf.glyph_infos

    return len(glyph_infos) == 1 and glyph_infos[0].cluster == 0, glyph_infos


def unicode(s):
    return "".join(f"\\U{ord(c):08X}" for c in s)


def from_unicode_escape(s):
    return codecs.decode(s, "unicode_escape")


# %% [markdown]
# Load font
file_path = "D2CodingLigature.ttf"

if not os.path.exists(file_path):
    print("downloading font..", end="")

    urllib.request.urlretrieve("https://mihaicosma.com/D2CodingLigature.ttf", file_path)
    print(". done")

if not os.path.exists(file_path):
    raise FileNotFoundError(f"File {file_path} not found")

with open(file_path, "rb") as fontfile:
    fontdata = fontfile.read()

font = hb.Font(hb.Face(hb.Blob(fontdata)))

# %% [markdown]
# Test with one emoji
# %%
teststring = "🤗"
print(teststring)
print(
    f"{teststring} suported={is_emoji_supported_by_font(hb, teststring)[0]}"
    f" unicode={unicode(teststring)} encoded={from_unicode_escape(unicode(teststring))}"
)

# ## [markdown]
# Run the whole test
# %%
supported_count = 0
unsupported_count = 0

if not os.path.exists("emoji-test.txt"):
    print("downloading emoji-test.txt..", end="")

    urllib.request.urlretrieve("https://unicode.org/Public/emoji/15.0/emoji-test.txt", "emoji-test.txt")
    print(". done")

with open("emoji-test.txt", "r", encoding="utf-8") as f:
    lines = f.readlines()
    for line in lines[:]:
        if line.startswith("#"):
            continue
        line = line.strip()
        if line == "":
            continue
        parts = line.split(";")
        if len(parts) < 2:
            continue
        code_point = parts[0].strip()
        rest = parts[1].strip()
        parts = rest.split("#")
        status = parts[0].strip()
        rest = parts[1].strip()
        parts = rest.split(" ")
        emoji = parts[0].strip()
        code_point = code_point.split(" ")
        double_wide = len(code_point) > 1
        unfullyqualiﬁed = status != "fully-qualified"
        res = is_emoji_supported_by_font(hb, emoji)
        if is_supported := res[0]:
            info: hb.GlyphInfo
            if res[1] and res[1][0]:
                info = res[1][0]
            try:
                name = " ".join(parts[1:])
            except Exception:
                name = ""
            encoded = from_unicode_escape(unicode(emoji))
            # numspaces = 5 - int(unfullyqualiﬁed) - (code_point[0] == "0000")
            numspaces = 5 + int(status == "unqualified")
            # print(code_point[0])
            print(
                f"{emoji}{' '*numspaces} {is_supported=} {numspaces=} {status=} {code_point=} unicode={unicode(emoji)}",
                end="",
            )
            print(f" encoded={encoded} name={name}")
            supported_count += 1
        else:
            unsupported_count += 1

print(f"{supported_count=:,.0f} {unsupported_count=:,.0f}")
