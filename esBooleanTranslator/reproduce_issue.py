
import sys
import os
import json

# Add current directory to path
sys.path.append(os.getcwd())

from esPreview.espreview import BooleanToDSLConverter

def test_tokenization():
    converter = BooleanToDSLConverter()
    query = '++"Amazon Prime"'
    
    print(f"Original Query: {query}")
    
    # 1. Tokenize
    tokens = converter._tokenize_with_quotes(query)
    print(f"Tokens: {tokens}")
    
    # 2. Normalize
    normalized = converter._normalize_tokens(tokens)
    print(f"Normalized: {normalized}")
    
    # 3. Convert
    dsl = converter.convert(query, ["headlines", "text"])
    print(f"DSL: {json.dumps(dsl, indent=2)}")

if __name__ == "__main__":
    test_tokenization()
