import json

chapter = '00_L00'
with open(f'outputs/chapters/{chapter}.json') as f:
    map_data = json.load(f)
    
# Check terrain
for row in map_data["map"]["terrain"]:
    print(row)