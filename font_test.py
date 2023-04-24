import harfbuzz as hb


def is_emoji_supported_by_font(emoji: str) -> bool:
    # Create text buffer:
    buf = hb.Buffer.create()
    buf.add_str(emoji)
    buf.guess_segment_properties()

    # Shape text:
    features = [hb.Feature("kern", 1), hb.Feature("liga", 1)]
    hb.shape(font, buf, features)

    # Check if the emoji is supported
    glyph_infos = buf.glyph_infos
    return len(glyph_infos) == 1 and glyph_infos[0].cluster == 0


with open("D2CodingLigature.ttf", "rb") as fontfile:
    fontdata = fontfile.read()

blob = hb.Blob.create(fontdata, length=len(fontdata), mode=1, user_data=None, destroy=None)
font = hb.Font.create(face=hb.Face.create(blob, 0, autoscale=False))

emoji = "😀"
print(is_emoji_supported_by_font(emoji))
