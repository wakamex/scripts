from pprint import isreadable
import harfbuzz as hb
import codecs
from typing import Tuple
from fontTools.ttLib import TTFont, ttGlyphSet


def is_emoji_supported_by_font(emoji: str) -> Tuple[bool, Tuple[hb.GlyphInfo]]:
    # Create text buffer:
    buf = hb.Buffer.create()
    buf.add_str(emoji)
    buf.guess_segment_properties()

    # Shape text:
    features = [hb.Feature("kern", 1), hb.Feature("liga", 1)]
    hb.shape(font, buf, features)

    # Check if the emoji is supported
    glyph_infos: Tuple[hb.GlyphInfo] = buf.glyph_infos

    return len(glyph_infos) == 1 and glyph_infos[0].cluster == 0, glyph_infos


def unicode(s):
    return "".join(f"\\U{ord(c):08X}" for c in s)


def from_unicode_escape(s):
    return codecs.decode(s, "unicode_escape")


file_path = "D2CodingLigature.ttf"

# tt = TTFont(file_path)
# print(f"{tt=} {dir(tt)=}")
# for d in dir(tt):
#     if d in ["glyphOrder"]:
#         continue
#     print(f"{d=} v={getattr(tt,d)}") if not d.startswith("_") else None

# print(f"{tt['maxp'].numGlyphs=}")

# cmap = tt.getBestCmap()
# cmapr = tt.getReverseGlyphMap()
# gs: dict[str, ttGlyphSet] = tt.getGlyphSet()
# print(f"{dir(gs)=}")
# for d in dir(gs):
#     if d in ["hMetrics"]:
#         continue
#     print(f"{d=} v={getattr(gs,d)}") if not d.startswith("_") else None
# i = 0
# for k, char in gs.items():
#     if i > 1000:
#         break
#     glyph = tt["glyf"][k]
#     if i == 0:
#         print("char methods:", [d for d in dir(char) if not d.startswith("_")])
#         print("glyph methods:", [d for d in dir(glyph) if not d.startswith("_")])
#     if glyph.numberOfContours == -1:
#         glyph_type = "compound glyph"
#     elif glyph.numberOfContours == 0:
#         glyph_type = "empty glyph"
#     elif glyph.numberOfContours > 0:
#         glyph_type = "simple glyph"
#     # gc = glyph.getMaxpValues()
#     # print(f"{gc=}")
#     id_ = cmapr[k]

#     # get character
#     # x = chr(id_)
#     x = cmap[id_]
#     # uc = unicode(x)
#     uc = ""
#     # de = from_unicode_escape(uc)
#     de = ""

#     print(f"{i=:5} {id_=:5} original:    {x:5} {uc=:15} {de}     ", end=" ")
#     name = char.name
#     lsb, tsb = char.lsb, char.tsb
#     height, width = char.height, char.width
#     # print(f"{k=}, {dir(v)=}")
#     print(f"{lsb=:4}, {tsb=} {height=}, {width=} {k=}")
#     i += 1

with open(file_path, "rb") as fontfile:
    fontdata = fontfile.read()

blob = hb.Blob.create(fontdata, length=len(fontdata), mode=1, user_data=None, destroy=None)
font = hb.Font.create(face=hb.Face.create(blob, 0, autoscale=False))

teststring = "🤗"
print(teststring)
print(
    f"{teststring} suported={is_emoji_supported_by_font(teststring)[0]}"
    f" unicode={unicode(teststring)} encoded={from_unicode_escape(unicode(teststring))}"
)

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
        res = is_emoji_supported_by_font(emoji)
        is_supported = res[0]
        if is_supported:
            info: hb.GlyphInfo
            if res[1]:
                if res[1][0]:
                    info = res[1][0]
                    # for i in res[1]:
                    #     print(f"{i=}")
            try:
                name = " ".join(parts[1:])
            except:
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
