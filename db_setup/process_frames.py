import os, io, csv, json
import requests, argparse
import pandas as pd
import numpy as np
from ast import literal_eval
from datetime import datetime
from panoptes_client import Project, Panoptes
from collections import OrderedDict, Counter
from sklearn.cluster import DBSCAN
import utils.db_utils as db_utils
from utils.zooniverse_utils import auth_session

def bb_iou(boxA, boxB):

    # Compute edges
    temp_boxA = boxA.copy()
    temp_boxB = boxB.copy()
    temp_boxA[2], temp_boxA[3] = (
        temp_boxA[0] + temp_boxA[2],
        temp_boxA[1] + temp_boxA[3],
    )
    temp_boxB[2], temp_boxB[3] = (
        temp_boxB[0] + temp_boxB[2],
        temp_boxB[1] + temp_boxB[3],
    )

    # determine the (x, y)-coordinates of the intersection rectangle
    xA = max(temp_boxA[0], temp_boxB[0])
    yA = max(temp_boxA[1], temp_boxB[1])
    xB = min(temp_boxA[2], temp_boxB[2])
    yB = min(temp_boxA[3], temp_boxB[3])

    # compute the area of intersection rectangle
    interArea = abs(max((xB - xA, 0)) * max((yB - yA), 0))
    if interArea == 0:
        return 1
    # compute the area of both the prediction and ground-truth
    # rectangles
    boxAArea = abs((temp_boxA[2] - temp_boxA[0]) * (temp_boxA[3] - temp_boxA[1]))
    boxBArea = abs((temp_boxB[2] - temp_boxB[0]) * (temp_boxB[3] - temp_boxB[1]))

    # compute the intersection over union by taking the intersection
    # area and dividing it by the sum of prediction + ground-truth
    # areas - the intersection area
    iou = interArea / float(boxAArea + boxBArea - interArea)

    # return the intersection over union value
    return 1 - iou


def filter_bboxes(total_users, users, bboxes, obj, eps, iua):

    # If at least half of those who saw this frame decided that there was an object
    user_count = pd.Series(users).nunique()
    if user_count / total_users >= obj:
        # Get clusters of annotation boxes based on iou criterion
        cluster_ids = DBSCAN(min_samples=1, metric=bb_iou, eps=eps).fit_predict(bboxes)
        # Count the number of users within each cluster
        counter_dict = Counter(cluster_ids)
        # Accept a cluster assignment if at least 80% of users agree on annotation
        passing_ids = [k for k, v in counter_dict.items() if v / user_count >= iua]

        indices = np.isin(cluster_ids, passing_ids)

        final_boxes = []
        for i in passing_ids:
            # Compute median over all accepted bounding boxes
            boxes = np.median(np.array(bboxes)[np.where(cluster_ids == i)], axis=0)
            final_boxes.append(boxes)

        return indices, final_boxes

    else:
        return [], bboxes


def main():
    "Handles argument parsing and launches the correct function."
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--user", "-u", help="Zooniverse username", type=str, required=True
    )
    parser.add_argument(
        "--password", "-p", help="Zooniverse password", type=str, required=True
    )
    parser.add_argument(
        "-db",
        "--db_path",
        type=str,
        help="the absolute path to the database file",
        default=r"koster_lab.db",
        required=True,
    )
    parser.add_argument(
        "-obj",
        "--object_thresh",
        type=float,
        help="Agreement threshold required among different users",
        default=0.8,
    )
    parser.add_argument(
        "-zw",
        "--zoo_workflow",
        type=float,
        help="Number of the Zooniverse workflow of interest",
        default=12852,
        required=False,
    )
    parser.add_argument(
        "-zwv",
        "--zoo_workflow_version",
        type=float,
        help="Version number of the Zooniverse workflow of interest",
        default=21.85,
        required=False,
    )
    parser.add_argument(
        "-eps",
        "--iou_epsilon",
        type=float,
        help="threshold of iou for clustering",
        default=0.5,
    )
    parser.add_argument(
        "-iua",
        "--inter_user_agreement",
        type=float,
        help="proportion of users agreeing on clustering",
        default=0.8,
    )
    parser.add_argument(
        "-nu",
        "--n_users",
        type=float,
        help="Minimum number of different Zooniverse users required per clip",
        default=5,
        required=False,
    )
    parser.add_argument(
        "-du",
        "--duplicates_file_id",
        help="Google drive id of list of duplicated subjects",
        type=str,
        required=False,
    )

    args = parser.parse_args()

    project = auth_session(args.user, args.password)

    # Get the export classifications
    export = project.get_export("classifications")

    # Save the response as pandas data frame
    rawdata = pd.read_csv(
        io.StringIO(export.content.decode("utf-8")),
        usecols=[
            "user_name",
            "subject_ids",
            "subject_data",
            "classification_id",
            "workflow_id",
            "workflow_version",
            "created_at",
            "annotations",
        ],
    )

    # Filter w2 classifications
    w2_data = rawdata[
        (rawdata.workflow_id == args.zoo_workflow)
        & (rawdata.workflow_version >= args.zoo_workflow_version)
    ].reset_index()

    # Clear duplicated subjects
    if args.duplicates_file_id:
        w2_data = db_utils.combine_duplicates(w2_data, args.duplicates_file_id)

    #Drop NaN columns
    w2_data = w2_data.drop(['dupl_subject_id', 'single_subject_id'], 1)

    ## Check if subjects have been uploaded
    # Get species id for each species
    conn = db_utils.create_connection(args.db_path)

    # Get subject table
    uploaded_subjects = pd.read_sql_query(
        "SELECT id FROM subjects WHERE subject_type='frame'", conn
    )

    # Add frame subjects to db that have not been uploaded
    new_subjects = w2_data[(~w2_data.subject_ids.isin(uploaded_subjects))]
    new_subjects["subject_dict"] = new_subjects["subject_data"].apply(lambda x: [v["retired"] for k,v in json.loads(x).items()][0])
    new_subjects = new_subjects[~new_subjects.subject_dict.isnull()].drop("subject_dict", 1)

    if len(new_subjects) > 0 and args.zoo_workflow_version > 30:

        # Get info of subjects uploaded to the project
        export = project.get_export("subjects")

        # Save the subjects info as pandas data frame
        subjects_df = pd.read_csv(
            io.StringIO(export.content.decode("utf-8")),
            usecols=["subject_id", "subject_set_id", "created_at"],
        )

        new_subjects = pd.merge(
            new_subjects,
            subjects_df,
            how="left",
            left_on="subject_ids",
            right_on="subject_id",
        )

        # Extract the video filename and annotation details
        new_subjects[
            [
                "frame_number",
                "frame_exp_sp_id",
                "movie_id",
                "classifications_count",
                "created_at",
                "retired_at",
                "retirement_reason",
            ]
        ] = pd.DataFrame(
            new_subjects["subject_data"]
            .apply(
                lambda x: [
                    {
                        "frame_number": v["frame_number"],
                        "frame_exp_sp_id": v["frame_exp_sp_id"],
                        "movie_id": v["movie_id"],
                        "classifications_count": v["retired"]["classifications_count"],
                        "created_at": v["retired"]["created_at"],
                        "retired_at": v["retired"]["retired_at"],
                        "retirement_reason": v["retired"]["retirement_reason"],
                    }
                    for k, v in json.loads(x).items()
                ][0]
            )
            .tolist()
        )

        new_subjects["subject_type"] = "frame"
        movies_df = pd.read_sql_query("SELECT id, filename FROM movies", conn)
        movies_df = movies_df.rename(
            columns={"id": "movie_id", "filename": "movie_filename"}
        )
        new_subjects = pd.merge(new_subjects, movies_df, how="left", on="movie_id")

        new_subjects["filename"] = new_subjects.apply(
            lambda x: x["movie_filename"] + "_" + str(x["frame_number"]) + ".jpg",
            axis=1,
        )
        # Set irrelevant columns to None
        new_subjects["clip_start_time"] = None
        new_subjects["clip_end_time"] = None
        new_subjects = new_subjects[
            [
                "subject_ids",
                "subject_type",
                "filename",
                "clip_start_time",
                "clip_end_time",
                "frame_exp_sp_id",
                "frame_number",
                "workflow_id",
                "subject_set_id",
                "classifications_count",
                "retired_at",
                "retirement_reason",
                "created_at",
                "movie_id",
            ]
        ]
        new_subjects = new_subjects.drop_duplicates(subset="subject_ids")
        db_utils.test_table(new_subjects, "subjects", keys=["movie_id"])

        # Add values to subjects
        db_utils.add_to_table(
            args.db_path, "subjects", [tuple(i) for i in new_subjects.values], 14
        )

    # Calculate the number of users that classified each subject
    w2_data["n_users"] = w2_data.groupby("subject_ids")["classification_id"].transform(
        "nunique"
    )

    # Select frames with at least n different user classifications
    w2_data = w2_data[w2_data.n_users >= args.n_users]

    # Drop workflow and n_users columns
    w2_data = w2_data.drop(
        columns=[
            "workflow_id",
            "workflow_version",
            "n_users",
            "created_at",
        ]
    )

    # Extract the video filename and annotation details
    subject_data_df = pd.DataFrame(
        w2_data["subject_data"]
        .apply(
            lambda x: [
                {
                    "movie_id": v["movie_id"],
                    "frame_number": v["frame_number"],
                    "label": v["label"],
                }
                for k, v in json.loads(x).items()  # if v['retired']
            ][0],
            1,
        )
        .tolist()
    )

    w2_data = pd.concat(
        [w2_data.reset_index().drop("index", 1), subject_data_df],
        axis=1,
        ignore_index=True,
    )
    w2_data = w2_data[w2_data.columns[1:]]
    pd.set_option('display.max_columns', None)

    w2_data.columns = [
        "classification_id",
        "user_name",
        "annotations",
        "subject_data",
        "subject_ids",
        "movie_id",
        "frame_number",
        "label",
    ]

    movies_df = pd.read_sql_query("SELECT id, filename FROM movies", conn)
    movies_df = movies_df.rename(columns={"id": "movie_id"})

    w2_data = pd.merge(w2_data, movies_df, how="left", on="movie_id")

    # Convert to dictionary entries
    w2_data["movie_id"] = w2_data["movie_id"].apply(lambda x: {"movie_id": x})
    w2_data["frame_number"] = w2_data["frame_number"].apply(
        lambda x: {"frame_number": x}
    )
    w2_data["label"] = w2_data["label"].apply(lambda x: {"label": x})
    w2_data["user_name"] = w2_data["user_name"].apply(lambda x: {"user_name": x})
    w2_data["subject_id"] = w2_data["subject_ids"].apply(lambda x: {"subject_id": x})
    w2_data["annotation"] = w2_data["annotations"].apply(
        lambda x: literal_eval(x)[0]["value"], 1
    )

    # Extract annotation metadata
    w2_data["annotation"] = w2_data[
        ["movie_id", "frame_number", "label", "annotation", "user_name", "subject_id"]
    ].apply(
        lambda x: [
            OrderedDict(
                list(x["movie_id"].items())
                + list(x["frame_number"].items())
                + list(x["label"].items())
                + list(x["annotation"][i].items())
                + list(x["user_name"].items())
                + list(x["subject_id"].items())
            )
            for i in range(len(x["annotation"]))
        ]
        if len(x["annotation"]) > 0
        else [
            OrderedDict(
                list(x["movie_id"].items())
                + list(x["frame_number"].items())
                + list(x["label"].items())
                + list(x["user_name"].items())
                + list(x["subject_id"].items())
            )
        ],
        1,
    )

    # Convert annotation to format which the tracker expects
    ds = [
        OrderedDict(
            {
                "user": i["user_name"],
                "movie_id": i["movie_id"],
                "label": i["label"],
                "start_frame": i["frame_number"],
                "x": int(i["x"]) if "x" in i else None,
                "y": int(i["y"]) if "y" in i else None,
                "w": int(i["width"]) if "width" in i else None,
                "h": int(i["height"]) if "height" in i else None,
                "subject_id": i["subject_id"] if "subject_id" in i else None,
            }
        )
        for i in w2_data.annotation.explode()
        if i is not None and i is not np.nan
    ]

    # Get prepared annotations
    w2_full = pd.DataFrame(ds)
    w2_annotations = w2_full[w2_full["x"].notnull()]

    new_rows = []
    final_indices = []
    for name, group in w2_annotations.groupby(["movie_id", "label", "start_frame"]):
        movie_id, label, start_frame = name

        total_users = w2_full[
            (w2_full.movie_id == movie_id)
            & (w2_full.label == label)
            & (w2_full.start_frame == start_frame)
        ]["user"].nunique()

        # Filter bboxes using IOU metric (essentially a consensus metric)
        # Keep only bboxes where mean overlap exceeds this threshold
        indices, new_group = filter_bboxes(
            total_users=total_users,
            users=[i[0] for i in group.values],
            bboxes=[np.array((i[4], i[5], i[6], i[7])) for i in group.values],
            obj=args.object_thresh,
            eps=args.iou_epsilon,
            iua=args.inter_user_agreement,
        )

        subject_ids = [i[8] for i in group.values[indices]]

        for ix, box in zip(subject_ids, new_group):
            new_rows.append(
                (
                    movie_id,
                    label,
                    start_frame,
                    ix,
                )
                + tuple(box)
            )

    w2_annotations = pd.DataFrame(
        new_rows,
        columns=[
            "movie_id",
            "label",
            "start_frame",
            "subject_id",
            "x",
            "y",
            "w",
            "h",
        ],
    )

    # Get species id for each species
    conn = db_utils.create_connection(args.db_path)

    # Get subject table
    subjects_df = pd.read_sql_query("SELECT id, frame_exp_sp_id FROM subjects", conn)

    subjects_df = subjects_df.rename(
        columns={"id": "subject_id", "frame_exp_sp_id": "species_id"}
    )

    w2_annotations = pd.merge(
        w2_annotations,
        subjects_df,
        how="left",
        left_on="subject_id",
        right_on="subject_id",
        validate="many_to_one",
    )

    # Filter out invalid movies
    w2_annotations = w2_annotations[w2_annotations["movie_id"].notnull()][
        ["species_id", "x", "y", "w", "h", "subject_id"]
    ]

    # Add values to agg_annotations_frame
    db_utils.add_to_table(
        args.db_path,
        "agg_annotations_frame",
        [(None,) + tuple(i) for i in w2_annotations.values],
        7,
    )

    print(f"Frame Aggregation Complete: {len(w2_annotations)} annotations added")


if __name__ == "__main__":
    main()
