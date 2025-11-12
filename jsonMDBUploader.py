import json
import os
from pymongo import MongoClient

client = MongoClient('mongodb+srv://mmfmrifath:8CJDr5O1wXffoV5i@paralegal-prod.zuwjzay.mongodb.net/')
db = client["paralegal_prod"]
collection = db["lex_prod"]

json_files_directory = 'data/legislations/legislation_D'

for filename in os.listdir(json_files_directory):
    if filename.endswith('.json'):
        filepath = os.path.join(json_files_directory, filename)
        with open(filepath, 'r') as f:
            data = json.load(f)
            if isinstance(data, list):  # If the file contains a JSON array
                collection.insert_many(data)
            else:  # If the file contains a single JSON object
                collection.insert_one(data)

print("JSON files imported successfully!")