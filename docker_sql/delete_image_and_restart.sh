docker stop $(docker ps -all | grep fetchlinks | awk '{ print $1 }')
docker rm $(docker ps -all | grep fetchlinks | awk '{ print $1 }')
docker rmi $(docker image list | grep fetchlinks | awk '{ print $3 }')
