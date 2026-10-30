# Human attempt at map highlight extraction
import os
import json
import cv2
import numpy as np

def find_map_bounds(labeled_map, original_map):
    """
    Find map bounds with cv2
    """
    labeled_img = cv2.imread(labeled_map)
    original_img = cv2.imread(original_map)
    
    labeled_height, labeled_width = labeled_img.shape[:2]
    original_height, original_width = original_img.shape[:2]
    
    # Find bounds of the labeled map
    left_x = 0
    bottom_y = labeled_height # Exclusive bound
    right_x = original_width * 2 # Exclusive bound
    top_y = bottom_y - original_height * 2 # Inclusive bound
    
    return (left_x, top_y, right_x, bottom_y)

def sample_tile(image, x, y, tile_size):
    """
    Sample a tile's background color from its 4 corners, returning
    the mean color in BGR format.
    """
    tile = image[y*tile_size:(y+1)*tile_size, x*tile_size:(x+1)*tile_size]
    margin = 2 if tile_size > 16 else 1 #Pixel margin to avoid including grid lines
    
    corners = [
        tile[margin:tile_size//4, margin:tile_size//4],
        tile[margin:tile_size//4, 3*tile_size//4:tile_size-margin],
        tile[3*tile_size//4:tile_size-margin, margin:tile_size//4],
        tile[3*tile_size//4:tile_size-margin, 3*tile_size//4:tile_size-margin],
    ]
    
    # Reshape into RGB array
    pixels = np.concatenate(
        [p.reshape(-1, 3) for p in corners if p.size],
        axis=0
    )

    # Mask all pure white and pure black pixels (text that is too close to the sides)
    is_white = np.all(pixels >= 250, axis=1)
    is_black = np.all(pixels <= 5, axis=1)

    valid_mask = ~(is_white | is_black)
    filtered_pixels = pixels[valid_mask]

    #print(pixels)
    return np.mean(filtered_pixels, axis=0)

HIGHLIGHT_COLORS = {
    "C":   np.array([255, 255, 0]),
    "G":  np.array([0, 255, 0]),
    "R":    np.array([0, 0, 255]),
    "Y": np.array([0, 255, 255]),
    "B":  np.array([0, 0, 0]),
}

def classify_highlight(labeled_color, original_color):
    """ Classifies a highlight as one of the important colors """
    best_color = "*"
    best_error = float("inf")
    best_alpha = None

    for name, highlight in HIGHLIGHT_COLORS.items():
        direction = highlight - original_color

        denom = np.dot(direction, direction)
        if denom == 0:
            continue
        # Estimate alpha (how much of the highlight is applied)
        alpha = np.dot(
            labeled_color - original_color, direction
        ) / denom

        alpha = np.clip(alpha, 0, 1)

        predicted = original_color + alpha * direction

        error = np.linalg.norm(labeled_color - predicted)

        # Put a minimum bound on alpha to avoid false positives
        if error < best_error and alpha > 0.1:
            best_error = error
            best_color = name
            best_alpha = alpha

    return best_color, best_alpha, best_error

def save_to_json(mask, output_path):
    """ Convert a mask to JSON format and save it """
    # Make lists of each color's positions
    data = {
        "Blue": [],
        "Green": [],
        "Red": [],
        "Yellow": [],
        "Black": []
    }
    letter_to_color = {
        "C": "Blue", #Cyan
        "G": "Green",
        "R": "Red",
        "Y": "Yellow",
        "B": "Black"
    }

    for letter in HIGHLIGHT_COLORS.keys():
        rows, cols = np.where(mask == letter)
        positions = [(int(col), int(row)) for col, row in zip(cols, rows)]
        data[letter_to_color[letter]] = positions

    with open(output_path, "w") as f:
        f.write("{\n")
        items = list(data.items())

        for i, (category, points) in enumerate(items):
            comma = "," if i < len(items) - 1 else ""
            f.write(f"  {json.dumps(category)}: {json.dumps(points)}{comma}\n")

        f.write("}\n")

def detect_highlighted_tiles(image_path, reference_path, grid_bounds):
    """ Runs highlight detection on an entire map """
    image = cv2.imread(image_path)
    labeled_image = image[grid_bounds[1]:grid_bounds[3], grid_bounds[0]:grid_bounds[2]]
    reference = cv2.imread(reference_path)

    labeled_tile_size = 32
    original_tile_size = 16
    rows = labeled_image.shape[0] // labeled_tile_size
    cols = labeled_image.shape[1] // labeled_tile_size

    mask = np.zeros((rows, cols), dtype=str)

    for y in range(rows):
        for x in range(cols):
            # Sample the tile from the labeled image
            current_color = sample_tile(labeled_image, x, y, labeled_tile_size)
            # Sample the tile from the reference image
            original_color = sample_tile(reference, x, y, original_tile_size)

            # Check for the best matching highlight
            highlight_type, alpha, error = classify_highlight(current_color, original_color)

            # Choose highlight type
            mask[y, x] = highlight_type

            # Get map name from path
            map_name = os.path.basename(image_path).split(".")[0]
            save_to_json(mask, f"data/maps/detection/{map_name}.json")

    return mask

np.set_printoptions(linewidth=np.inf)

map_ids = [str(i) for i in range(9)]
map_ids += [str(i) + "a" for i in range(9, 17)]
map_ids += [str(i) + "b" for i in range(9, 17)]
map_ids += [str(i) for i in range(17, 22)]
map_ids += ["5x", "21x"]

for map_id in map_ids:
    print(f"Processing map ID {map_id}...")
    map_bounds = find_map_bounds(f"data/maps/labeled/{map_id}.png", f"data/maps/original/{map_id}.png")
    print(detect_highlighted_tiles(f"data/maps/labeled/{map_id}.png", f"data/maps/original/{map_id}.png", map_bounds))
    print("*" * 50)

