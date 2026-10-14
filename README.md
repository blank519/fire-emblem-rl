# Fire Emblem ML/RL
This project has 2 parts for now:
1. Training and comparing different Fire Emblem map generation techniques.
2. Trying to train a Reinforcement Learning agent to play Fire Emblem 8.

## Part 0?: Data Extraction
This part is focused on extracting data from an FE8 GBA ROM to be used in training.

### extract.py
Python script that extracts item, class, character, and chapter data from an FE8 GBA ROM and saves it in JSON format. More information can be found in the [scripts/data_extraction/EXTRACTION_README.md](scripts/data_extraction/EXTRACTION_README.md) file.
Unsure how much of the extracted data I will use for now, but it's good to have anyways.
Admittedly, this part was written with an LLM because I do not have any experience digging through a ROM's memory layout.

#### DATA EXTRACTED:
Item:
- Name
- Stats (number of uses, accuracy/might for weapons and magic, etc.)
- Weapon, staff, item, and magical flag

Class:
- Name
- Movement speed
- Stat base, max, and growth rates (hp, str, mag, skl, spd, etc.)
- Weapon level ranks
- Interaction with each terrain type
- Class-specific abilities (Canto, Crit bonus, Pierce, etc.)

Character:
- Name
- Starting Class
- Stat base and growth rates (hp, str, mag, skl, spd, etc.)
- Starting weapon level ranks
- Affinity (determines stats boosted by Supports)

Chapter:
- Name
- Terrain grid
- (Tentative) unit starting locations  
*Notable limitation*: the extractor is unable to locate the starting locations of units on the map when a chapter begins, because the stored unit positions are used in pre-chapter cutscenes.

### map_to_data.py
Converts the terrain grid from extracted chapter data into a NumPy array of tile indices for easier processing. Saves the array as a .npy file in the `data/` directory.  
Also generates extra data by rotating and flipping each grid, increasing the number of maps to train on from ~60 to ~480.

## Part 1: Map Generation
This part is focused on generating maps for Fire Emblem games using different techniques. All map generators are intended to use the terrain grids saved by `map_to_data.py`.

### ngram_baseline_pcg.py
Generates maps using a simple n-gram-based approach.

#### STEPS:
1. For every extracted terrain grid:
   - Create 2D "n-grams" in a n-radius square around each tile from the padded grid
   - Count the frequency of each n-gram
2. Combine all n-gram counts from all grids into a "model"
3. Generate new maps by sampling from the n-gram model
   - For each tile in an empty grid, match the n-radius square around it with n-grams in the model
   - Treat unchosen tiles as wildcards that fit any tile
   - Randomly choose 1 n-gram from all matches found based on the frequency of each n-gram
   - If no matches were found, flip 1 random tile in the window to a wildcard and try again

Samples were taken from n-gram models created with radius 1, 2, and 3, with both the original and augmented (rotated/flipped) terrain grids.

#### RESULTS:
| Data Type | Radius | Time | Results |
| -------- | -------- | -------- | -------- |
| Original Grids | 1 | 2.35s | Simple, large chunks of same terrain. Looks alright |
| All Grids  | 1  | 9.97s  | Simple, large chunks of same terrain. Looks alright |
| Original Grids | 2 | 28.19s | More complicated, good results |
| All Grids  | 2  | 171.59s  | More complicated, alright results |
| Original Grids | 3 | 145.27s | Overly complicated/scattered |
| All Grids  | 3  | 897.85s  | Overly complicated/scattered |

N-gram models using all grids (original + augmented) take much longer to generate from and produce messier results.

### Other Ideas
- Fully Connected CNN
- GAN
- Diffusion model
- RNN/Transformer?

## Part 2: FE Agent
This part is focused on training a Reinforcement Learning agent to play Fire Emblem 8.  
Might use a generator from part 1 to create training maps for more generalization - TBD.


## Part 3?: Live Test
Testing a trained agent in a live game session. Though these 2 scripts were the first that were created, they will likely be unused until a trained agent is available.

### fe8_state.lua
mGBA script that reads the live game state and serves it via HTTP.

### fe8_bridge.py
Python client that reads the live game state from mGBA via HTTP.
