# %%
from matplotlib import colors

def darken_color(color, amount=0.4):
    """Darkens the given color by the specified amount."""
    # Convert the color to RGB.
    rgb = colors.hex2color(color)

    # Darken the color.
    rgb_dark = [max(c - amount, 0) for c in rgb]

    # Convert the color back to hexadecimal.
    hex_dark = colors.rgb2hex(rgb_dark)

    return hex_dark

# The colors to darken.
colors_to_darken = ["#b5bd68", "#b9ca4a"]

# Darken the colors.
darkened_colors = [darken_color(color) for color in colors_to_darken]

darkened_colors

# %%
