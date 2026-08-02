""" Baseline, Non-ML method: use N-gram statistics from existing maps to generate new maps """

import numpy as np
import time
from pathlib import Path

from terrain_viz import save_grid_panels

def pad_grid(grid, radius):
    return np.pad(grid, radius, mode='constant', constant_values=0)

def generate_ngrams(file_path, radius=1):
    """ Generates a dictionary of n-gram counts from a grid file """
    grid = np.load(file_path)
    padded_grid = pad_grid(grid, radius)
    ngrams = {}

    # Create 2D n-grams from the padded grid
    for i in range(radius, padded_grid.shape[0] - radius):
        for j in range(radius, padded_grid.shape[1] - radius):
            ngram = padded_grid[i-radius:i+radius+1, j-radius:j+radius+1]
            if tuple(ngram.flatten()) not in ngrams:
                ngrams[tuple(ngram.flatten())] = 0
            ngrams[tuple(ngram.flatten())] += 1
    
    return ngrams

def combine_all_ngrams(ngram_dict_list):
    """ Combines multiple n-gram dictionaries into one """
    combined_ngrams = {}
    for ngram_dict in ngram_dict_list:
        for ngram, count in ngram_dict.items():
            if ngram not in combined_ngrams:
                combined_ngrams[ngram] = 0
            combined_ngrams[ngram] += count
    
    return combined_ngrams

def create_ngram_model(grid_files, radius=1):
    """ Create an ngram model from a list of grid files """
    ngram_dict_list = [generate_ngrams(file, radius=radius) for file in grid_files]
    combined_ngrams = combine_all_ngrams(ngram_dict_list)
    return combined_ngrams

def sample_from_ngrams(combined_ngrams, radius, width, height, rng=np.random.default_rng()):
    """ Samples a new map from the combined n-gram statistics """
    # Initialize empty grid, where -1 represent an unchosen tile
    grid = np.full((height, width), -1, dtype=int)
    padded_grid = pad_grid(grid, radius)
    center = radius * (2 * radius + 1) + radius
    
    # Search ngrams for matching patterns and fill in the grid
    for i in range(radius, padded_grid.shape[0] - radius):
        for j in range(radius, padded_grid.shape[1] - radius):
            window = padded_grid[i-radius:i+radius+1, j-radius:j+radius+1]
            flattened_window = window.flatten()
            
            # Match ngrams with current window while treating -1 as a wild card (matches any tile number)
            # Note that presumably, the center tile should always be -1 (unchosen) since we're filling it in
            matched = False
            while not matched:
                matches = {}
                for ngram in combined_ngrams:
                    if all(flattened_window[k] == ngram[k] or flattened_window[k] == -1 for k in range(len(ngram))):
                        matches[ngram] = combined_ngrams[ngram]
                
                # Choose a random matching ngram based on frequency and fill in the center tile
                if matches:
                    # Transform counts into probabilities
                    total_count = sum(matches.values())
                    probabilities = [count / total_count for count in matches.values()]
                    
                    chosen_ngram = rng.choice(list(matches.keys()), p=probabilities)
                    padded_grid[i, j] = chosen_ngram[center]
                    matched = True
                else:
                    # If no match was found, retry by turning one random non-wildcard tile to a wildcard (-1)
                    flippable_indices = [k for k in range(len(flattened_window)) if flattened_window[k] != -1]
                    random_index = rng.choice(flippable_indices)
                    flattened_window[random_index] = -1

    grid = padded_grid[radius:-radius, radius:-radius]
    return grid

def main():
    """ Main function to generate and save a new map """
    # Load all grid files
    dir_path = Path("data/terrain_grids")
    grid_files = [str(file) for file in dir_path.glob("*.npy")]
    original_files = [file for file in grid_files if "_rot" not in file and "_flip" not in file]

    # Create models and sample a new map for radius sizes 1, 2, and 3
    for radius in range(1, 4):    
        # Create 2 ngram models: one for original grids and one including augmented grids
        print(f"Creating ngram models with radius {radius}")
        ngram_model_original = create_ngram_model(original_files, radius=radius)
        ngram_model_all = create_ngram_model(grid_files, radius=radius)

        start_time = time.perf_counter()
        print(f"Sampling with radius {radius}")
        rng = np.random.default_rng(seed=42)
        print("Sampling from original ngram model...")
        new_map_original = sample_from_ngrams(ngram_model_original, radius=radius, width=20, height=20, rng=rng)
        end_time = time.perf_counter()
        print(f"Time taken for original ngram model: {end_time - start_time:.2f} seconds")
        print("Sampling from all ngram model...")
        new_map_all = sample_from_ngrams(ngram_model_all, radius=radius, width=20, height=20, rng=rng)
        end_time = time.perf_counter()
        print(f"Time taken for all ngram model: {end_time - start_time:.2f} seconds")
        
        # Save the new maps
        out_dir = Path("outputs/grids")
        out_dir.mkdir(parents=True, exist_ok=True)
        np.savetxt(out_dir / "new_map_original.txt", new_map_original, fmt="%d")
        np.savetxt(out_dir / "new_map_all.txt", new_map_all, fmt="%d")

        # Save a colored, labeled visualization of both maps for easy inspection
        # (written into outputs/grids/grid_visuals/ by terrain_viz).
        viz_path = save_grid_panels(
            [("original ngram model", new_map_original),
            ("all (augmented) ngram model", new_map_all)],
            f"new_maps_radius_{radius}.png",
            suptitle=f"N-gram generated terrain (radius={radius})",
        )
        print(f"Saved visualization to {viz_path}")

if __name__ == "__main__":
    main()