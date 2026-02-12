"""Create a synthetic test room image for spike testing.

Generates a simple but recognizable room scene using Pillow geometry:
walls, floor, furniture silhouettes. Good enough for testing annotation
drawing and API call structure, but real room photos should be used
for quality evaluation.
"""

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1024, 1024


def create_room_image() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), "#F5F0E8")
    draw = ImageDraw.Draw(img)

    # Floor
    draw.polygon([(0, 600), (1024, 600), (1024, 1024), (0, 1024)], fill="#C4A882")

    # Back wall
    draw.rectangle([(0, 0), (1024, 600)], fill="#E8E0D0")

    # Left wall (perspective)
    draw.polygon([(0, 0), (200, 100), (200, 550), (0, 600)], fill="#D8D0C0")

    # Right wall (perspective)
    draw.polygon([(1024, 0), (824, 100), (824, 550), (1024, 600)], fill="#D8D0C0")

    # Window on back wall
    draw.rectangle([(400, 150), (624, 400)], fill="#87CEEB", outline="#8B7355", width=3)
    draw.line([(512, 150), (512, 400)], fill="#8B7355", width=2)
    draw.line([(400, 275), (624, 275)], fill="#8B7355", width=2)

    # Sofa
    draw.rounded_rectangle([(250, 480), (600, 580)], radius=10, fill="#4A6741")
    draw.rounded_rectangle([(260, 440), (590, 490)], radius=8, fill="#3D5636")

    # Coffee table
    draw.rectangle([(350, 600), (550, 660)], fill="#6B4226", outline="#4A2E18", width=2)

    # Floor lamp (right side)
    draw.rectangle([(720, 300), (730, 560)], fill="#2F2F2F")
    draw.ellipse([(690, 260), (760, 310)], fill="#FFD700", outline="#DAA520", width=2)

    # Rug
    draw.ellipse([(300, 650), (700, 850)], fill="#8B4513", outline="#654321", width=2)

    # Plant (left side)
    draw.rectangle([(230, 500), (260, 560)], fill="#8B4513")
    draw.ellipse([(210, 440), (280, 510)], fill="#228B22")
    draw.ellipse([(220, 420), (270, 480)], fill="#2E8B2E")

    # Add label
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except (OSError, IOError):
        font = ImageFont.load_default()
    draw.text((10, 10), "Test Room (Synthetic)", fill="#666666", font=font)

    return img


if __name__ == "__main__":
    img = create_room_image()
    img.save("spike/test_images/test_room.png")
    print("Created spike/test_images/test_room.png")
