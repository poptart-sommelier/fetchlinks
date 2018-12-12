#TODO: FLOW OF EXECUTION
#TODO: RUN TWITTERLINKS, REDDITLINKS, RSSLINKS
#TODO: THEY ALL RETURN THEIR OWN JSON STRUCTURE
#TODO: EACH MUST GENERATE UNIQUE ID FOR EACH LINK (HASH TITLE+DATE+SOURCE?)
#TODO: DETERMINE FIELDS, THEN INSERT EVERYTHING INTO DB, CHECKING FOR DUPLICATES VIA UNIQUE ID

#TODO: DB SHOULD BE INSTANTIATED HERE AND PASSED TO ALL FUNCTIONS
#TODO: STAND UP DOCKER CONTAINER WITH MYSQLDB
#TODO: CONFIG IS READ IN HERE AND SETTINGS ARE PASSED ON TO EACH MODULE
#TODO: CONFIG: cred file location, all rss feeds to pull, all subreddits to parse.

#DB STRUCTURE:
#TITLE, DESCRIPTION, AUTHOR, SOURCE, UID, URLS, DATE_CREATED

import json
# import twitter_no_wrapper
import reddit_links
# import rss_links

CONFIG_LOCATION = 'data/config/config.json'

# Check if ./data exists, if not, create it.

# Read in credentials file, assign to proper variables

# Instantiate DB connection

# Instantiate Logging

# Call each module, passing in db_conn, log object, and config data


def parse_config(config_file):
    pass


with open(CONFIG_LOCATION, 'r') as config_file:
    config = config_file.read()

print(json.dumps(reddit_links.main()))

print()