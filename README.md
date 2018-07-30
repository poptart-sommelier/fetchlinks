Remaining work:


1) Add dates to tweets
2) Error logging for success/failure - WORKING - DB_LOAD, MISSING - TWITTER_NO_WRAPPER
3) orchestration script that wraps scraper and db loader
5) cron job to delete archives older than X days (30?)
6) double check flask app, make sure nothing too confidential
7) move flask app to docker container
8) architecture:
a) scraper, db loader run in one container
b) mysql in container
c) flask app in container
d) docker swarm to stand them all up
e) script to configure them all on launch, pulling down scripts from github for scraper, loader, and db configuration, configure cron jobs and env variables