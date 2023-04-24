import codecs


def unicode(s):
    return "".join(f"\\U{ord(c):08X}" for c in s)


def from_unicode_escape(s):
    return codecs.decode(s, "unicode_escape")


teststring = "🤗"
expected = "\\U0001F917"
uc = unicode(teststring)
print(f"{uc=}")
print(f"{expected=}")
print(f"{uc == expected=}")

# Convert back to emoji
emoji = from_unicode_escape(uc)
print(f"{emoji=}")
