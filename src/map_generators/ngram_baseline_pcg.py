""" Baseline, Non-ML method: use N-gram statistics from existing maps to generate new maps """

import numpy as np
from pathlib import Path

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
                    print(f"Match found for window at ({i}, {j})")
                    chosen_ngram = rng.choice(list(matches.keys()))
                    padded_grid[i, j] = chosen_ngram[center]
                    matched = True
                else:
                    print(f"No matching ngram found for window at ({i}, {j})")
                    # If no match was found, retry by turning one random tile to a wildcard (-1)
                    random_index = rng.integers(low=0, high=len(flattened_window) - 1)
                    flattened_window[random_index] = -1

    grid = padded_grid[radius:-radius, radius:-radius]
    return grid

def main():
    """ Main function to generate and save a new map """
    # Load all grid files
    dir_path = Path("data/terrain_grids")
    grid_files = [str(file) for file in dir_path.glob("*.npy")]
    original_files = [file for file in grid_files if "_rot" not in file and "_flip" not in file]
    
    # Create 2 ngram models: one for original grids and one including augmented grids
    ngram_model_original = create_ngram_model(original_files, radius=1)
    ngram_model_all = create_ngram_model(grid_files, radius=1)

    # Sample a new map
    rng = np.random.default_rng(seed=42)
    print("Sampling from original ngram model...")
    new_map_original = sample_from_ngrams(ngram_model_original, radius=1, width=10, height=10, rng=rng)
    print("Sampling from all ngram model...")
    new_map_all = sample_from_ngrams(ngram_model_all, radius=1, width=10, height=10, rng=rng)
    
    # Save the new maps
    np.savetxt("outputs/grids/new_map_original.txt", new_map_original, fmt="%d")
    np.savetxt("outputs/grids/new_map_all.txt", new_map_all, fmt="%d")

if __name__ == "__main__":
    main()