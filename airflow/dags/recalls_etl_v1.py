## import modules
import pandas as pd
from airflow import DAG
import airflow
from airflow.operators.python import PythonOperator
import json
import requests
from datetime import datetime
import psycopg2
from sqlalchemy import create_engine

# define database cedentials for database connection
HOST_NAME = 'recalls_db'
DATABASE = 'recalls_db'
USER_NAME = 'admin'
PASSWORD = 'admin'
PORT_ID = 5432

# define url for calling data from government data site
raw_url = 'https://recalls-rappels.canada.ca/sites/default/files/opendata-donneesouvertes/HCRSAMOpenData.json'

# data path to save raw data and cleaned data
raw_data_path = '/opt/airflow/data/raw/recalls_raw.csv'
cleaned_data_path = '/opt/airflow/data/cleaned/recalls_cleaned.csv'

# define a dag variable based on the Airflow DAG object
dag = DAG(
    dag_id='recalls_etl_v1',
    start_date=airflow.utils.dates.days_ago(0),
    schedule_interval=None

)

# define the extraction function that takes API url
# and save the raw data as a csv
def get_recall_data(url, raw_output_path):
    response = requests.get(url)
    response_data = response.json()
    print(response)

    recall_list = []

    for r in response_data:
        recall = {
            'recall_id': r['NID'],
            # 'title': r['Title'],
            'organization': r['Organization'],
            'product_name': r['Product'],
            'issue': r['Issue'],
            'category': r['Category'],
            'updated_at': r['Last updated']
        }
        recall_list.append(recall)

    print(f"Added {len(recall_list)} recalled records.")

    recall_df = pd.DataFrame(recall_list)

    current_timestamp = datetime.now()

    recall_df['data_received_at'] = current_timestamp

    recall_df.to_csv(raw_output_path, index=False)

# define a transformation function and save the cleaned data into another csv
def clean_recall_data(raw_input_path, cleaned_output_path):
    df = pd.read_csv(raw_input_path)

    df[['issue_category', 'category']] = df['category'].str.split(' - ', n=1, expand=True)
    df['updated_at'] = pd.to_datetime(df['updated_at'], format='%Y-%m-%d')

    df = df.sort_values(by=['updated_at'], ascending=False)
    df.to_csv(cleaned_output_path, index=False)

    print(f"Success: cleaned {len(df)} recall records.")

# define a function to load the data to postgresql database
# and use pd.sql() to load the transformed data to the database directly
def load_to_db(db_host, db_name, db_user, db_pswd, db_port, data_path):
    df = pd.read_csv(data_path)
    current_timestamp = datetime.now()
    df['data_ingested_at'] = current_timestamp

    # load csv data to the database
    engine = create_engine(f"postgresql+psycopg2://{db_user}:{db_pswd}@{db_host}/{db_name}")
    df.to_sql('recalls', con=engine, schema='data', if_exists='replace', index=False)

    print(f"Success: Loaded {len(df)} recall records to {db_name}.")


# define task using Airflow PythonOperator for raw data extraction
get_raw_data = PythonOperator(
    task_id='get_raw_data',
    python_callable=get_recall_data,
    op_kwargs={
        'url': raw_url,
        'raw_output_path': raw_data_path
    },
    dag=dag,
)

#define tasks to transform raw data
clean_raw_data = PythonOperator(
    task_id='clean_raw_data',
    python_callable=clean_recall_data,
    op_kwargs={
        'raw_input_path': raw_data_path,
        'cleaned_output_path': cleaned_data_path
    },
    dag=dag,
)

# define task using Airflow PythonOperator to load cleaned data to PostgreSQL database
load_data_to_db = PythonOperator(
    task_id='load_data_to_db',
    python_callable=load_to_db,
    op_kwargs={
        'db_host': HOST_NAME,
        'db_name': DATABASE,
        'db_user': USER_NAME,
        'db_pswd': PASSWORD,
        'db_port': PORT_ID,
        'data_path': cleaned_data_path
    },
    dag=dag,
)

# set the order to execute each of the tasks in the DAG
get_raw_data >> clean_raw_data >> load_data_to_db