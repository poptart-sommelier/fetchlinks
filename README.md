Remaining work:

1) Error logging for success/failure
2) orchestration script that wraps scaper and db loader
3) db loader should read all files in a dir, load them all, move them to a storage dir and archive them
4) cron job to delete archives older than X days (30?)
5) double check flask app, make sure nothing too confidential
6) move flask app to docker container
7) architecture:
a) scraper, db loader run in one container
b) mysql in container
c) flask app in container
d) docker swarm to stand them all up
e) script to configure them all on launch, pulling down scripts from github for scraper, loader, and db configuration