
Download elasticsearch https://www.elastic.co/downloads/elasticsearch

$​ ​ apt-get​ ​ install​ ​ ~/Downloads/elasticsearch-5.6.4.deb

$​ ​ systemctl​ ​ enable​ ​ elasticsearch

$​ ​ systemctl​ ​ start​ ​ elasticsearch


/etc/elasticsearch/elasticsearch.yml

http.cors.enabled:​ ​ true

http.cors.allow-origin:​ ​ "*"

