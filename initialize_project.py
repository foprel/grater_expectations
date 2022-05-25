from argparse import ArgumentParser
import os
import shutil
import ruamel.yaml as yaml
import uuid
import logging

# Logger
logging.basicConfig(
    level=logging.DEBUG,
    datefmt="%Y-%m-%d %H:%M:%S",
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def main_program():
    # -- 1. Parse arguments passed at runtime from terminal
    logger.info("Parsing command line arguments")
    parser = ArgumentParser()
    parser.add_argument("-p", "--project", type=str, required=True)
    args = parser.parse_args()

    # -- 2. Load and check configurations
    logger.info("Loading configurations from testing_config.yml")
    with open("testing_config.yml") as cfg_yaml:
        # -- Load
        cfg_all = yaml.safe_load(cfg_yaml)
        cfg_global = cfg_all["global"]

        try:
            cfg = cfg_all[args.project]
        except KeyError as ke:
            logger.warning(
                f"The project configurations for {args.project} were not found in the"
                " testing_config.yml. Please add it."
            )
            raise ke

        # -- Check global config
        global_keys = ["account_id", "region"]
        evaluate_global_config(cfg_global, global_keys, "global")

        # -- Check project config
        project_keys = [
            "store_bucket",
            "store_bucket_prefix",
            "site_bucket",
            "site_bucket_prefix",
            "docker_image_name",
            "site_name",
            "expectations_suite_name",
            "checkpoint_name",
            "run_name_template",
            "data_bucket",
            "prefix_data",
        ]
        evaluate_config_keys(cfg, project_keys, args.project)

    # -- 3. Check if the project exists and if so, if the user wants to continue. Then,
    # Create directory if needed
    _ = check_if_project_exists(args)
    if args.project not in os.listdir():
        os.mkdir(args.project)

    # -- 4. Copy bootstrap files (do this before copying any other files, as it might
    # overwrite and remove previously copied files)
    copy_and_overwrite_tree(
        from_path="bootstrap_files",
        to_path="/".join([".", args.project]),
        ignore_pattern=shutil.ignore_patterns(
            "__init__*", "non_verbose_files", "tutorial_files"
        ),
    )

    # -- 5. Copy configuration
    generate_project_config(cfg, args, cfg_global)

    # -- 6. Generate Great Expectations config yml
    generate_ge_config(cfg, args)

    # -- 7. Generate bash script for building Docker image and push to ECR
    generate_ecr_bash_script(cfg, args, cfg_global)

    # -- 8. Generate Terraform var files
    generate_terraform_var_files(cfg, args, cfg_global)

    # -- 9. Add provider.tf in each Terraform directory
    generate_terraform_provider_config(args, cfg_global)

    # -- 10. If tutorial, overwrite files with tutorial equivalents and add
    #       tutorial data
    adjust_for_tutorial(args)

    # -- 11. Start testing suite notebook
    # start_testing_suite_notebook(args)


# Functions
def evaluate_config_keys(cfg: dict, list_keys: list, config_name: str):
    """Function to evaluate if the passed configurations contain the required keys to
    bootstrap a new GE project

    Parameters
    ----------
    cfg : dict
        The passed config to be evaluated
    list_keys : list
        The list of keys the config should contain
    config_name : str
        The name of the config passed, used to make potential errors more understandable

    Raises
    ------
    KeyError
        A KeyError is raised if the configuration does not contain all required keys
    """
    # -- 1. Check for missing keys
    logger.info(f"Checking if required keys can be found in the {config_name} config")
    missing_keys = [key for key in list_keys if key not in cfg.keys()]
    if len(missing_keys) > 0:
        logger.warning(
            f"Not all keys found in {config_name} config. Missing: {missing_keys}"
        )
        raise KeyError(
            f"The {config_name} configuration is missing the "
            f"following arguments: {missing_keys}"
        )

    # -- 2. Check for missing values
    missing_values = [
        key for key, value in cfg.items() if not value if key != "site_bucket_prefix"
    ]
    if len(missing_values) > 0:
        logger.warning(
            f"Not all keys found in {config_name} have values. Missing: {missing_values}"
        )
        raise KeyError(
            f"The {config_name} configuration is missing values for the "
            f"following arguments: {missing_values}"
        )

    pass


def evaluate_global_config(cfg: dict, list_keys: list, config_name: str):
    """Function to evaluate if the passed global configuration contains the required 
    keys to and has proper values set for its parameters to bootstrap a new GE project

    Parameters
    ----------
    cfg : dict
        The passed config to be evaluated
    list_keys : list
        The list of keys the config should contain
    config_name : str
        The name of the config passed, used to make potential errors more understandable

    Raises
    ------
    KeyError
        A KeyError is raised if the configuration does not contain all required keys
    """
    # -- 1. Check for presence of keys
    evaluate_config_keys(cfg, list_keys, config_name)

    # -- 2. Check if default values have been adjusted
    DEFAULT = "MUST_BE_SET"
    failed = False
    list_failed = []
    for key in list_keys:
        if cfg[key] == DEFAULT:
            list_failed.append(key)
            logger.warning(
                f"The value for {key} in the global config is set to its default value."
                " Please fill in a proper value for this parameter."
            )
            failed = True

    if failed:
        raise ValueError(
            "Parameters were found in the global config that are still set to their "
            "default values. Please enter proper values for these parameters. The "
            f"values that must be set are for: {list_failed}"
        )
    else:
        pass


def check_if_project_exists(args):
    """Function to check if the project already exists and ensure that the user wants
    it to be overwritten, if that is the case"""
    if args.project in os.listdir():
        logger.info(
            f"The project you are trying to create, {args.project}, "
            "already exists in this repository. Are you sure you want to initialize "
            "this project again and overwrite existing files (y/[n])? "
        )
        response = input("Input: ")
        if response in ["y", "Y", "yes", "Yes", "YES"]:
            logger.info(f"Overwriting existing project: {args.project}")
        else:
            raise SystemExit("Project already exists, stopping initialization")


def generate_project_config(cfg: dict, args, cfg_global: dict = None):
    """Function to copy and write project specific configurations

    Parameters
    ----------
    cfg : dict
        Project config containing required elements to generate the GE configuration file
    args:
        Command line arguments passed at runtime. Expected to contain --project/-p
    cfg_global : dict
        Global config containing AWS account details. Are added to the configuration
        YAML if passed at runtime, otherwise they are skipped. By default, None
    """
    # -- Check for global configs, add if passed
    if cfg_global:
        cfg = {**cfg, **cfg_global}

    # -- Write file
    doc_out = yaml.dump(cfg, default_flow_style=False)
    with open(f"./{args.project}/project_config.yml", "w") as project_yaml:
        project_yaml.write(doc_out)


def generate_ge_config(cfg: dict, args):
    """Function to generate a configuration file for Great Expectations, using arguments
    passed through a config in cfg and command line arguments in args

    Parameters
    ----------
    cfg : dict
        Project config containing required elements to generate the GE configuration file
    args:
        Command line arguments passed at runtime. Expected to contain --project/-p
    """
    logger.info("Generating Great Expectations configuration file")
    path = f"./{args.project}/"
    base_yaml = f"""
  # config_version refers to the syntactic version of this config file, and is used in maintaining backwards compatibility
  # It is auto-generated and usually does not need to be changed.
  config_version: 3.0\n
  # Datasources tell Great Expectations where your data lives and how to get it.
  datasources:
    runtime_data:
      execution_engine:
        module_name: great_expectations.execution_engine
        class_name: PandasExecutionEngine
      data_connectors:
        runtime_data_connector:
          batch_identifiers:
            - batch_identifier
          class_name: RuntimeDataConnector\n
  stores:
  # Stores are configurable places to store things like Expectations, Validations
  # Data Docs, and more. These are for advanced users only - most users can simply
  # leave this section alone.
  #
  # Three stores are required: expectations, validations, and
  # evaluation_parameters, and must exist with a valid store entry. Additional
  # stores can be configured for uses such as data_docs, etc.
    expectations_store:
      class_name: ExpectationsStore
      store_backend:
        class_name: TupleS3StoreBackend
        bucket: {cfg["store_bucket"]}
        prefix: {cfg["store_bucket_prefix"]}/expectations/\n
    validations_store:
      class_name: ValidationsStore
      store_backend:
        class_name: TupleS3StoreBackend
        bucket: {cfg["store_bucket"]}
        prefix: {cfg["store_bucket_prefix"]}/validations/\n
    evaluation_parameter_store:
      class_name: EvaluationParameterStore\n
    checkpoint_store:
      class_name: CheckpointStore
      store_backend:
        class_name: TupleS3StoreBackend
        bucket: {cfg["store_bucket"]}
        prefix: {cfg["store_bucket_prefix"]}/checkpoints/\n
    profiler_store:
      class_name: ProfilerStore
      store_backend:
        class_name: TupleS3StoreBackend
        bucket: {cfg["store_bucket"]}
        prefix: {cfg["store_bucket_prefix"]}/profiles/
        suppress_store_backend_id: true\n
  expectations_store_name: expectations_store
  validations_store_name: validations_store
  evaluation_parameter_store_name: evaluation_parameter_store
  checkpoint_store_name: checkpoint_store
  profiler_store_name: profiler_store\n
  data_docs_sites:
  # Data Docs make it simple to visualize data quality in your project. These
  # include Expectations, Validations & Profiles. The are built for all
  # Datasources from JSON artifacts in the local repo including validations &
  # profiles from the uncommitted directory. Read more at https://docs.greatexpectations.io/en/latest/reference/core_concepts/data_docs.html
    {cfg["site_name"]}:
      class_name: SiteBuilder
      # set to false to hide how-to buttons in Data Docs
      show_how_to_buttons: true
      store_backend:
        class_name: TupleS3StoreBackend
        bucket: {cfg["site_bucket"]}
        prefix: {cfg["site_bucket_prefix"]}
      site_index_builder:
        class_name: DefaultSiteIndexBuilder


  anonymous_usage_statistics:
    enabled: true
    data_context_id: {str(uuid.uuid4())}
  """
    if "great_expectations" not in os.listdir(path):
        os.mkdir(path + "great_expectations")

    with open(
        f"./{args.project}/great_expectations/great_expectations.yml", "w"
    ) as out:
        out.write(base_yaml)


def generate_ecr_bash_script(cfg: dict, args, cfg_global: dict):
    """Function to generate a bash script to create a docker image and push it to ECR,
    using arguments from configs in cfg and cfg_global and command line arguments in args

    Parameters
    ----------
    cfg : dict
        Config containing required elements to generate the GE configuration file
    args:
        Command line arguments passed at runtime. Expected to contain --project/-p
    cfg_global : dict
        Config containing required global elements (AWS account and region) to generate
        the GE configuration file
    """
    logger.info(
        "Generating bash script for making docker image and uploading it to ECR"
    )
    path = f"./{args.project}/"
    ECR_endpoint = (
        f'{cfg_global["account_id"]}.dkr.ecr.{cfg_global["region"]}.amazonaws.com'
    )
    docker_image = cfg["docker_image_name"]
    region = cfg_global["region"]
    document = f"""#!/bin/bash
    
# Ensure region is (temporarily) set to prevent errors
export AWS_DEFAULT_REGION="eu-west-1"

# Change permissions of files. Otherwise upstream lambda will give permission error
chmod 644 $(find . -type f)
chmod 755 $(find . -type d)

# Temporarily copy requirements.txt for usage w/ Dockerfile
cp ../requirements.txt ./requirements.txt

# Build image
docker build -t {docker_image} .

# Remove requirements.txt
rm -rf requirements.txt

# Log into AWS and ECR
aws ecr get-login-password --region {region} | docker login --username AWS --password-stdin {ECR_endpoint}

# Create test repo, gives warning but continues of it already exists
aws ecr create-repository --repository-name {docker_image} --image-scanning-configuration scanOnPush=true --image-tag-mutability MUTABLE || true

# Tag image and push to ECR
docker tag {docker_image}:latest {ECR_endpoint}/{docker_image}:latest
docker push {ECR_endpoint}/{docker_image}:latest"""

    with open(f"{path}/build_image_store_on_ecr.sh", "w") as out:
        out.write(document)


def generate_terraform_provider_config(args, cfg_global: dict):
    """Function to generate Terraform provider configuration files for each Terraform
    directory within a project

    Parameters
    ----------
    args:
        Command line arguments passed at runtime. Expected to contain --project/-p
    cfg_global : dict
        Global config containing AWS account details
    """
    # -- 1. Generate document
    document = f"""# NOTE: It's best to run Terraform using state stored in a state bucket. For more
# information, please refer to https://www.terraform.io/language/state/remote
terraform {{
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 4.0.0"
    }}
  }}
}}

provider "aws" {{
  region = "{cfg_global["region"]}"
}}
"""

    # -- 2. Put in all Terraform directories
    tf_dir = f"{args.project}/terraform/"
    for dir in os.listdir(tf_dir):
        abs_path = os.path.abspath(tf_dir + dir)
        abs_path_file = abs_path + "/provider.tf"
        with open(abs_path_file, "w") as out:
            out.write(document)


def generate_terraform_var_files(cfg: dict, args, cfg_global: dict):
    """Function to generate Terraform variable files that can be used in combination with
    Terraform configuration files to spin up the required AWS services

    Parameters
    ----------
    cfg : dict
        Config containing required elements to generate the GE configuration file
    args:
        Command line arguments passed at runtime. Expected to contain --project/-p
    cfg_global : dict
        Global config containing AWS account details
    """
    # -- 1. Generate Terraform vars for buckets
    path = f"./{args.project}/"
    document_buckets = f"""ge-bucket-name      = "{cfg["store_bucket"]}"
ge-site-bucket-name = "{cfg["site_bucket"]}"
ge-data-bucket-name = "{cfg["data_bucket"]}"
"""

    # -- 2. Generate Terraform vars for lambda
    image_uri = (
        f'"{cfg_global["account_id"]}.dkr.ecr.{cfg_global["region"]}.amazonaws.com/'
        f'{cfg["docker_image_name"]}:latest"'
    )
    document_lambda = document_buckets + f"image_uri = {image_uri}"

    # -- 3. Write files
    paths_out = [
        f"./{args.project}/terraform/buckets/{args.project}.auto.tfvars",
        f"./{args.project}/terraform/lambda/{args.project}.auto.tfvars",
    ]
    for path, doc in zip(paths_out, [document_buckets, document_lambda]):
        with open(path, "w") as out:
            out.write(doc)


def copy_and_overwrite_tree(
    from_path: str, to_path: str, ignore_pattern: shutil.ignore_patterns = None
):
    """Helper function to copy files and overwrite existing ones if necessary

    Parameters
    ----------
    from_path : str
        Source path of the directory to copy
    to_path : str
        Destination path of directory to paste
    ignore_pattern : shutil.ignore_patterns, optional
        Set of patterns for files that should not be copied
    """
    logger.info(f"Copying and overwriting files from {from_path} to {to_path}")
    if os.path.exists(to_path):
        shutil.rmtree(to_path)
    shutil.copytree(from_path, to_path, ignore=ignore_pattern)


def adjust_for_tutorial(args):
    """Helper function to move files into tutorial directory if tutorial is being run"""
    logger.info("Making adjustments for running the tutorial")
    if args.project == "tutorial":
        # -- 1. Add tutorial Terraform files
        # -- .1 Buckets
        orig = "bootstrap_files/tutorial_files/terraform/tutorial_bucket/"
        dest = f"{args.project}/terraform/buckets/"
        for tutorial_file in os.listdir(orig):
            shutil.copy2(orig + tutorial_file, dest + tutorial_file)

        # -- .2 Lambda
        orig = "bootstrap_files/tutorial_files/terraform/tutorial_lambda/"
        dest = f"{args.project}/terraform/lambda/"
        for tutorial_file in os.listdir(orig):
            shutil.copy2(orig + tutorial_file, dest + tutorial_file)

        # -- 2. Add tutorial data
        orig = "bootstrap_files/tutorial_files/tutorial_data"
        dest = f"{args.project}/data"
        copy_and_overwrite_tree(orig, dest)

        # -- 3. Copy tutorial notebook and remove expectation_suite.ipynb
        orig = "bootstrap_files/tutorial_files/tutorial_notebook.ipynb"
        dest = f"{args.project}/tutorial_notebook.ipynb"
        shutil.copy2(orig, dest)
        os.remove(f"{args.project}/expectation_suite.ipynb")

        # -- 4. Replace lambda function
        orig = "bootstrap_files/tutorial_files/lambda_function.py"
        dest = f"{args.project}/lambda_function.py"
        shutil.copy2(orig, dest)


def start_testing_suite_notebook(args):
    """Helper function to open up the expectation_suite.ipynb notebook upon
    initialization of a new project"""
    logger.info(f"Opening testing suite notebook for {args.project}")
    path = f"./{args.project}"
    os.system(f"nbopen {path}/expectation_suite.ipynb")


if __name__ == "__main__":
    main_program()
