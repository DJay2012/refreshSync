
import sys
import os
import json
import logging
from pathlib import Path

# Add the directory containing espreview to path
current_dir = Path(__file__).parent
sys.path.append(str(current_dir / "esPreview"))

from esPreview.espreview import ESPreviewEngine, ESPreviewConfig, BooleanToDSLConverter

def test_complex_query():
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    
    # The full query provided by the user
    query = """("Amazon" OR "Amazon.in" OR "Amazon India") AND (("Amazon" NEAR/12 ("books" OR "book" OR "children’s books" OR "children's books" OR "kids books" OR "kid's books" OR "literature" OR "fiction" OR ++"non-fiction" OR "study guides" OR "exam guides" OR "engineering books" OR "medical books" OR "law books" OR "biographies" OR "true accounts" OR "hardcover" OR "paperback" OR "board book" OR "Kindle" OR "Kindle Unlimited" OR "Kindle eBook" OR "KDP" OR ++"self-publishing" OR "audiobook" OR "ebooks" OR ++"e-books")) OR ("Amazon" NEAR/12 ("toys" OR "toy" OR "soft toys" OR "learning toys" OR "STEM kit" OR "STEM kits" OR "board games" OR "puzzles" OR "action figures" OR "dolls" OR "kids toys" OR "art supplies" OR "craft kits" OR "coloring book" OR "school supplies" OR "stationery" OR "gaming accessories" OR "video games" OR "game discs" OR "software" OR "CD" OR "CDs")) OR (("Great Indian Festival" OR "Super Value Days" OR "Great Freedom Festival" OR "Wardrobe Refresh Sale" OR "Smartphone Upgrade Days") NEAR/12 ("books" OR "toys" OR "gaming" OR "gaming accessories" OR "video games" OR "CDs" OR "software")) OR (("PlayStation" OR "PS4" OR "PS5" OR "Xbox" OR "XBOX" OR "Nintendo Switch" OR "gaming console") NEAR/15 ("Amazon" OR "Amazon.in" OR "Amazon India" OR "Great Indian Festival" OR "Prime Day" OR "offers" OR "sale" OR "discount"))) AND (NOT (("Amazon" NEAR/5 ("rainforest" OR "rain forest" OR "forest" OR "river" OR "basin" OR "jungle" OR "tribe" OR "indigenous")))) AND (NOT ((++"Prime Video" OR ++"PRIME VIDEO" OR ++"Amazon Prime Video" OR ++"AMAZON PRIME VIDEO") NEAR/8 ("web series" OR "series" OR "show" OR "shows" OR "season" OR "episode" OR "episodes" OR "film" OR "movie" OR "movies" OR "trailer" OR "teaser" OR "originals"))) AND (NOT (("review" OR "specs" OR "processor" OR "display" OR "benchmark" OR "chipset" OR "refresh rate" OR "unboxing" OR "HDR" OR "resolution" OR "performance" OR "battery life") NEAR/8 ("laptop" OR "smartphone" OR "TV" OR "tablet" OR "earbuds" OR "router" OR "camera" OR "headphones" OR "monitor" OR "smartwatch")))"""
    
    print("=" * 80)
    print("TESTING COMPLEX QUERY BREAKDOWN")
    print("=" * 80)
    
    try:
        # Initialize Config
        config = ESPreviewConfig()
        config.es_host = "https://elastic.pnq.co.in/"
        config.es_user = "pnqIndex"
        config.es_password = "New#pnq#Change!"
        
        # Initialize Engine
        engine = ESPreviewEngine(config)
        
        # Breakdown queries
        q_gaming = '("PlayStation" OR "PS4" OR "PS5" OR "Xbox" OR "XBOX" OR "Nintendo Switch" OR "gaming console") NEAR/15 ("Amazon" OR "Amazon.in" OR "Amazon India" OR "Great Indian Festival" OR "Prime Day" OR "offers" OR "sale" OR "discount")'
        
        q_not_rainforest = '(NOT (("Amazon" NEAR/5 ("rainforest" OR "rain forest" OR "forest" OR "river" OR "basin" OR "jungle" OR "tribe" OR "indigenous"))))'
        q_not_prime_video = '(NOT ((++"Prime Video" OR ++"PRIME VIDEO" OR ++"Amazon Prime Video" OR ++"AMAZON PRIME VIDEO") NEAR/8 ("web series" OR "series" OR "show" OR "shows" OR "season" OR "episode" OR "episodes" OR "film" OR "movie" OR "movies" OR "trailer" OR "teaser" OR "originals")))'
        q_not_tech_specs = '(NOT (("review" OR "specs" OR "processor" OR "display" OR "benchmark" OR "chipset" OR "refresh rate" OR "unboxing" OR "HDR" OR "resolution" OR "performance" OR "battery life") NEAR/8 ("laptop" OR "smartphone" OR "TV" OR "tablet" OR "earbuds" OR "router" OR "camera" OR "headphones" OR "monitor" OR "smartwatch")))'

        queries_to_test = {
            "Calibration 1 (Five NEAR/10 November)": '("Five" NEAR/10 "November")',
            "Calibration 2 (Amazon NEAR/100 PlayStation)": '("PlayStation" NEAR/100 "Amazon")',
            "1. Gaming Part Only (NEAR/15)": '("PlayStation" OR "PS4" OR "PS5" OR "Xbox" OR "XBOX" OR "Nintendo Switch" OR "gaming console") NEAR/15 ("Amazon" OR "Amazon.in" OR "Amazon India" OR "Great Indian Festival" OR "Prime Day" OR "offers" OR "sale" OR "discount")',
            "Full Query": query
        }

        with open("debug_output.txt", "w", encoding='utf-8') as f:
            f.write("Starting Debug Session\n")
            
            # Check Connection
            try:
                # We need to access the client from the engine? 
                # ESPreviewEngine creates self.es_client
                info = engine.es_client.info()
                f.write(f"Connected to ES: {info.get('version', {}).get('number')}\n")
            except Exception as e:
                f.write(f"Connection Failed: {e}\n")
            
            for q_name, q_str in queries_to_test.items():
                f.write(f"\n[Executing {q_name}]\n")
                f.write(f"Query: {q_str[:200]}...\n")
                
                try:
                    res = engine.execute_query(q_str, include_content=False)
                    f.write(f"Success: {res.success}\n")
                    f.write(f"Matches: {res.total_matches}\n")
                    if res.errors:
                        f.write(f"Errors: {res.errors}\n")
                    
                    for idx, i_res in res.index_results.items():
                        f.write(f"  {idx}: {i_res.total_hits} hits\n")
                        
                except Exception as exc:
                    f.write(f"Exception executing {q_name}: {exc}\n")

            f.write("\n[Done Debugging]\n")

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_complex_query()
