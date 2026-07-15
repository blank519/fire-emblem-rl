""" Convert extracted map data to training format """

from pathlib import Path
import json
import numpy as np

dir_path = Path("outputs/chapters")
data_path = Path("data/terrain_grids")
data_path.mkdir(exist_ok=True)

for file_path in dir_path.glob("*.json"):
    with open(file_path, "r") as f:
        data = json.load(f)
    terrain_grid = data["map"]["terrain"]
    print(f"File: {file_path}")
    file_name = file_path.name.split('\\')[-1].replace(".json", "")

    #Ignore 28_I15 to 35_I21x because they are duplicates of 15_E15 to 22_E21x
    if file_name.startswith(("28", "29", "30", "31", "32", "33", "34", "35")):
        continue

    # Convert to numpy array
    numpy_grid = np.array(terrain_grid)
    
    # For every numpy grid, save as .npy file
    np.save(data_path / file_name, numpy_grid)

    # Generate more data by rotating and flipping the grid
    flipped_grid = np.fliplr(numpy_grid)
    np.save(data_path / f"{file_name}_flip_h.npy", flipped_grid)
    
    for i in range(3):
        # Rotate 90 degrees clockwise
        rotated_grid = np.rot90(numpy_grid, i)
        np.save(data_path / f"{file_name}_rot{i}.npy", rotated_grid)
        
        # Flip horizontally
        flipped_grid = np.fliplr(rotated_grid)
        np.save(data_path / f"{file_name}_rot{i}_flip_h.npy", flipped_grid)