# %%
def parse_room_dimensions(text):
    """Parse room dimensions from a multiline string and calculate square footage."""
    lines = text.strip().split('\n')
    room_info = []
    for line in lines:
        parts = line.split('(')
        room_name = parts[0].strip()
        dimensions_part = parts[1].split('）')[0]  # Adjusted to handle special character
        dimensions = dimensions_part.split('x')
        length, width = map(float, [dim.strip().split(' ')[0] for dim in dimensions])  # Extract numbers only
        room_info.append((room_name, length, width))
    return room_info

def calculate_square_footage(rooms):
    """Calculate square footage from room dimensions."""
    meters_to_feet = 3.28084
    square_footages = []
    for room in rooms:
        square_footages.append((room[1] * meters_to_feet) * room[2] * meters_to_feet)
    return square_footages

def main(room_text):
    rooms = parse_room_dimensions(room_text)
    square_footages = calculate_square_footage(rooms)
    print(f"{square_footages=}")
    total_square_footage = sum(square_footages)
    for idx,sqft in enumerate(square_footages):
        print(f"{rooms[idx]}: {sqft:.2f} sq ft")
    print(f"Total Square Footage: {total_square_footage:.2f} sq ft")

# 720 hamlet
room_text = """
Kitchen(3.4 x 3.7 m）Level: Main
Dining Rm(3.7 x 2.6 m）Level: Main
Living Rm(3.8 x 3.5 m）Level: Main
Bath 4-Piece(3.3 x 2.2 m）Level: Main
Bedroom(2.7 x 3.1 m）Level: Main
Primary Bedrm(3.4 x 5.2 m）Level: Main
Walk-In Closet(2.3 x 3 m）Level: Main
Recreation Rm(8.8 x 5 m）Level: Lower
Bedroom(3.7 x 2.8 m）Level: Lower
Bedroom(3.2 x 3.4 m）Level: Lower
Bath 3-Piece(3.8 x 2 m）Level: Lower
"""

# 758 hamlet
room_text = """
Living Rm(3.6 x 4.1 m）Level: Main
Dining Rm(3.1 x 3.3 m）Level: Main
Kitchen(3.6 x 3.2 m）Level: Main
Primary Bedrm(4 x 3.2 m）Level: Main
Bedroom(3 x 4.1 m）Level: Main
Bedroom(2.8 x 3 m）Level: Main
Bath 4-Piece(1.5 x 2.2 m）Level: Main
Recreation Rm(8.8 x 7.4 m）Level: Lower
Bath 3-Piece(3.8 x 2 m）Level: Lower
"""

main(room_text)
# %%