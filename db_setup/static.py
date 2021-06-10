import os, cv2, csv, json, sys, io
import operator, argparse, requests
import pandas as pd
import sqlite3
from datetime import datetime
import utils.db_utils as db_utils

def get_length(video_file):
    final_fn = video_file if os.path.isfile(video_file) else db_utils.unswedify(video_file)
    if os.path.isfile(final_fn):
        cap = cv2.VideoCapture(final_fn)
        fps = cap.get(cv2.CAP_PROP_FPS)     
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        length = frame_count/fps
    else:
        length, fps = None, None
    return fps, length


def add_sites(sites_csv):

    # Load the csv with sites information
    sites_df = db_utils.download_csv_from_google_drive(sites_csv)
    
    # Select relevant fields
    sites_df = sites_df[
        ["koster_site_id", "siteName", "decimalLatitude", "decimalLongitude", "geodeticDatum", "countryCode"]
    ]
    
    ### TODO add a roadblock to prevent empty lat/long/datum/countrycode
   

    # Add values to sites table
    db_utils.add_to_table(
        db_path, "sites", [tuple(i) for i in sites_db.values], 6
    )

    
def add_movies(movies_csv, movies_path):

    # Load the csv with movies information
    movies_df = db_utils.download_csv_from_google_drive(movies_csv)

    # Include server's path to the movie files
    movies_df["Fpath"] = movies_path + "/" + movies_df["filename"]
    
    # Check that videos can be mapped
    movies_df['exists'] = movies_df['Fpath'].map(os.path.isfile)
    
    # Standarise the filename
    movies_df["filename"] = movies_df["filename"].str.normalize("NFD")
    
    # Ensure all videos have fps and duration information
    if movies_df["fps", "duration"].isna() == True:
            
            # Select only those movies with missing fps or duration
            missing_fps_movies = movies_df["fps", "duration"].isna()
            
            # Prevent missing fps and duration information
            if len(missing_fps_movies[~missing_fps_movies.exists]) > 0:
                print(
                    f"There are {len(missing_fps_movies) - missing_fps_movies.exists.sum()} out of {len(missing_fps_movies)} movies missing from the server without fps and duration information"
                )

                return
            
            else: 
                # Calculate the fps and length of the original movies
                movies_df[["fps", "duration"]] = pd.DataFrame(missing_fps_movies["Fpath"].apply(get_length, 1).tolist(), columns=["fps", "duration"])
            
                print(
                    f" The fps and duration of {len(missing_fps_movies)} movies have been succesfully added"
                )
    
    
    # Ensure date is ISO 8601:2004(E) compatible
    #try:
    #    date.fromisoformat(movies_df['eventDate'])
    #except ValueError:
    #    print("Invalid eventDate column")

    # Reference movies with their respective sites
    sites_df = pd.read_sql_query("SELECT id, name FROM sites", conn)
    sites_df = sites_df.rename(columns={"id": "Site_id"})

    movies_df = pd.merge(
        movies_df, sites_df, how="left", on="siteName"
    )

    
    # Select only those fields of interest
    movies_db = movies_df[
        ["koster_movie_id", "filename", "created_on", "fps", "duration", "Author", "Site_id", "Fpath"]
    ]

    # Add values to movies table
    db_utils.add_to_table(
        db_path, "movies", [ tuple(i) for i in movies_db.values], 8
    )


def add_species(species_csv, db_path):

    # Download the csv with species information from the google drive
    species_df = db_utils.download_csv_from_google_drive(species_csv)
    
    # Select relevant fields
    species_df = sites_df[
        ["koster_species_id", "commonName", "scientificName", "taxonRank", "kingdom"]
    ]
    
    # Add values to species table
    db_utils.add_to_table(
        db_path, "species", [tuple([i]) for i in species_df.values], 5
    )


def main():
    "Handles argument parsing and launches the correct function."
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sites_csv",
        "-sit",
        help="Filepath of the csv file with info about the sites",
        type=str,
        required=True,
    )
    parser.add_argument(
        "--movies_csv",
        "-mov",
        help="Filepath of the csv file with info about the movies",
        type=str,
        required=True,
    )
    parser.add_argument(
        "--species_csv",
        "-sp",
        help="Filepath of the csv file with info about the species",
        type=str,
        required=True,
    )
    parser.add_argument(
        "-mp",
        "--movies_path",
        type=str,
        help="Absolute path to the movie files",
        default=r"/uploads",
    )

    args = parser.parse_args()

    # Connect to koster database
    conn = db_utils.create_connection("../koster_lab.db")
    
    add_sites(args.sites_csv)
    add_movies(args.movies_csv, args.movies_path)
    add_species(args.species_csv)


if __name__ == "__main__":
    main()
