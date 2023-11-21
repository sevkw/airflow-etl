# Another version of ETL DAG that interacts with AWS S3 bucket

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
from io import StringIO
## import AWS SDK boto3 module
import boto3
from botocore.exceptions import NoCredentialsError
import os
from dotenv import load_dotenv

# define database cedentials for database connection
HOST_NAME = 'recalls_db'
DATABASE = 'recalls_db'
USER_NAME = 'admin'
PASSWORD = 'admin'
PORT_ID = 5432

load_dotenv()
# AWS Credentials
AWS_ACCESS_KEY = os.getenv('AWS_ACCESS_KEY')
AWS_SECRET_KEY = os.getenv('AWS_SECRET_KEY')
BUCKET_NAME = 'recalls-data'



# define url for calling data from government data site
raw_url = 'https://recalls-rappels.canada.ca/sites/default/files/opendata-donneesouvertes/HCRSAMOpenData.json'

# data path to save raw data and cleaned data
raw_data_path = 'raw_data/recalls_raw.csv'
cleaned_data_path = 'cleaned_data/recalls_cleaned.csv'

# define a dag variable based on the Airflow DAG object
dag = DAG(
    dag_id='recalls_etl_s3',
    start_date=airflow.utils.dates.days_ago(0),
    schedule_interval=None

)

# define the extraction function that takes API url
# and save the raw data as a csv
def get_recall_data(url, bucket_name, raw_output_path):
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

    # Convert DataFrame to CSV String
    csv_buffer = StringIO()
    recall_df.to_csv(csv_buffer, index=False)

    # Initialize an S3 client
    s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)

    # Upload the DataFrame as CSV to S3
    s3.put_object(Body=csv_buffer.getvalue(), Bucket=bucket_name, Key=raw_output_path)


# define a transformation function and save the cleaned data into another csv
def clean_recall_data(raw_input_path, cleaned_output_path, bucket_name):
    # Initialize an S3 client
    s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)

    # Download CSV file from S3
    response = s3.get_object(Bucket=bucket_name, Key=raw_input_path)
    csv_content = response['Body'].read().decode('utf-8')
    
    df = pd.read_csv(StringIO(csv_content))

    df[['issue_category', 'category']] = df['category'].str.split(' - ', n=1, expand=True)
    df['updated_at'] = pd.to_datetime(df['updated_at'], format='%Y-%m-%d')

    df = df.sort_values(by=['updated_at'], ascending=False)

    # Convert DataFrame to CSV String
    csv_buffer = StringIO()
    df.to_csv(csv_buffer, index=False)

    # Upload the DataFrame as CSV to S3
    s3.put_object(Body=csv_buffer.getvalue(), Bucket=bucket_name, Key=cleaned_output_path)

    print(f"Success: cleaned {len(df)} recall records.")

# define a function to load the data to postgresql database
# and use pd.sql() to load the transformed data to the database directly
def load_to_db(db_host, db_name, db_user, db_pswd, db_port, data_path, bucket_name):
    s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)

     # Download CSV file from S3
    response = s3.get_object(Bucket=bucket_name, Key=data_path)
    csv_content = response['Body'].read().decode('utf-8')

    df = pd.read_csv(StringIO(csv_content))

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
        'bucket_name': BUCKET_NAME,
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
        'cleaned_output_path': cleaned_data_path,
        'bucket_name': BUCKET_NAME
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
        'data_path': cleaned_data_path,
        'bucket_name': BUCKET_NAME
    },
    dag=dag,
)

# set the order to execute each of the tasks in the DAG
get_raw_data >> clean_raw_data >> load_data_to_db