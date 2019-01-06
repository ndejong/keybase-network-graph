#!/usr/bin/env python3

import os
import json
import time
import argparse
import requests
import pygraphml
from bs4 import BeautifulSoup


class KeybaseNetworkGraphException(Exception):
    pass


class KeybaseNetworkGraph:

    name = 'Keybase Network Grapher'
    sleep_interval = 0.01

    args = None
    graph = None
    data_path = None
    max_depth = None

    data = {}

    def __init__(self):
        parser = argparse.ArgumentParser(description=self.name)
        parser.add_argument('--uid', type=str, metavar='<uid>', required=True,
                            help='Initial uid to start collecting data on, required.')
        parser.add_argument('--depth', type=int, metavar='<depth>', default=1,
                            help='Maximum depth of user connections to collect data on, default = 1')
        parser.add_argument('--path', type=str, metavar='<path>', default='/tmp/keybase-network-graph',
                            help='Maximum depth of user connections to collect data down to, default = /tmp/keybase-network-graph')
        parser.add_argument('--nograph', action='store_true', default=False,
                            help='Prevent GraphML generation.')
        self.args = parser.parse_args()

    def main(self):
        self.data_path = self.args.path
        self.max_depth = self.args.depth

        if self.args.nograph is False:
            self.graph = pygraphml.Graph()

        uid = self.args.uid
        while uid is not None:
            self.process_uid(uid)
            time.sleep(self.sleep_interval)
            uid = self.find_next_uid()

        if self.graph is not None:
            graph_parser = pygraphml.GraphMLParser()
            graph_filename = os.path.join(self.data_path, self.args.uid + '.graphml')
            graph_parser.write(self.graph, graph_filename)
            self.out('GraphML file: {}'.format(graph_filename))

    def find_next_uid(self):
        for uid in self.data.keys():
            if 'followers' not in self.data[uid] or self.data[uid]['followers'] is None:
                if self.data[uid]['depth'] <= self.max_depth:
                    return uid
            elif 'userdata' not in self.data[uid] or self.data[uid]['userdata'] is None:
                if self.data[uid]['depth'] <= self.max_depth:
                    return uid
        return None

    def process_uid(self, uid, use_datastore=True):

        if uid not in self.data:
            self.data[uid] = {}
        if 'depth' not in self.data[uid]:
            self.data[uid]['depth'] = 0
        if 'graph_node' not in self.data[uid] and self.graph is not None:
            self.data[uid]['graph_node'] = self.graph.add_node(uid)

        self.out('processing:{} depth:{} uid_total:{}'.format(uid, self.data[uid]['depth'], len(self.data.keys())))

        if self.data[uid]['depth'] > self.max_depth:
            self.out('max_depth_limited:{} '.format(uid))
            return

        userdata, self.data[uid]['userdata'] = self.get_userdata(uid, datastore=use_datastore)
        self.data[uid]['username'] = userdata['userdata']['them'][0]['basics']['username']

        if 'graph_node' in self.data[uid]:
            self.data[uid]['graph_node']['username'] = self.data[uid]['username']

        followers, self.data[uid]['followers'] = self.get_followers(uid, datastore=use_datastore)
        followers_added = self.process_followers(uid, followers['uids_followers'], direction='in')
        followers_added += self.process_followers(uid, followers['uids_following'], direction='out')

        self.out('complete:{} followers_added:{}'.format(uid, followers_added))
        self.out('===')

    def process_followers(self, uid, followers, direction):
        followers_added = 0
        for follower_uid in followers:
            if follower_uid not in self.data:
                self.data[follower_uid] = {}
                followers_added += 1
            if 'depth' not in self.data[follower_uid]:
                self.data[follower_uid]['depth'] = self.data[uid]['depth'] + 1
            if 'graph_node' not in self.data[follower_uid] and self.graph is not None:
                self.data[follower_uid]['graph_node'] = self.graph.add_node(follower_uid)
            if self.graph is not None:
                if direction == 'out':
                    self.graph.add_edge(self.data[uid]['graph_node'], self.data[follower_uid]['graph_node'],
                                        directed=True)
                elif direction == 'in':
                        self.graph.add_edge(self.data[follower_uid]['graph_node'], self.data[uid]['graph_node'],
                                            directed=True)
                else:
                    raise KeybaseNetworkGraphException('unexpected direction')
        return followers_added

    def get_uid_datapath(self, uid, make_path=False):
        path = os.path.join(self.data_path, uid[0:2], uid[2:4])
        if make_path is True and not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
        return path

    def get_uid_followers_filename(self, uid):
        return os.path.join(self.get_uid_datapath(uid), uid) + '_followers.json'

    def get_uid_userdata_filename(self, uid):
        return os.path.join(self.get_uid_datapath(uid), uid) + '_userdata.json'

    def get_followers(self, uid, datastore=True):
        datastore_filename = self.get_uid_followers_filename(uid)
        if datastore is True and os.path.isfile(datastore_filename):
            with open(datastore_filename) as f:
                return json.load(f), datastore_filename

        data = {
            'followers': self.request_followers(uid=uid, reverse=0),
            'uids_followers': [],
            'following': self.request_followers(uid=uid, reverse=1),
            'uids_following': [],
            'timestamp': int(time.time()),
        }

        data['uids_followers'].extend([
            d['uid'] for d in data['followers'] if ('uid' in d and d['uid'] not in data['uids_followers'])
        ])
        data['uids_following'].extend([
            d['uid'] for d in data['following'] if ('uid' in d and d['uid'] not in data['uids_following'])
        ])

        if datastore is True:
            self.get_uid_datapath(uid, make_path=True)
            with open(datastore_filename, 'w') as f:
                json.dump(data, f)
        return data, datastore_filename

    def get_userdata(self, uid, datastore=True):
        datastore_filename = self.get_uid_userdata_filename(uid)
        if datastore is True and os.path.isfile(datastore_filename):
            with open(datastore_filename) as f:
                return json.load(f), datastore_filename

        data = {
            'userdata': self.request_userdata(uid=uid),
            'timestamp': int(time.time())
        }
        if datastore is True:
            self.get_uid_datapath(uid, make_path=True)
            with open(datastore_filename, 'w') as f:
                json.dump(data, f)
        return data, datastore_filename

    def request_followers(self, uid, reverse, num=100):
        url = 'https://keybase.io/_/api/1.0/user/load_more_followers.json'
        params = {
            'reverse': int(reverse),
            'uid': uid,
            'last_uid': uid,
            'num_wanted': num
        }

        r = requests.get(url, params)
        if r.status_code != 200:
            raise KeybaseNetworkGraphException('request_followers() received a non http 200 response', r.content)

        if 'snippet' not in r.json():
            raise KeybaseNetworkGraphException('request_followers() did not respond with expected snippet data', r.content)
        else:
            snippet = r.json()['snippet']

        soup = BeautifulSoup(snippet, 'html.parser')

        followers = []
        for follower in soup.find_all('tr'):
            if not follower.find('a', class_='username'):
                continue
            user = {
                'uid': follower.get('data-uid'),
                'username': follower.find('a', class_='username').get_text(),
                'name': follower.find('span', class_='small').get_text(),
                'image': follower.find('img', class_='img-circle').get('src'),
            }
            followers.append(user)

        return followers

    def request_userdata(self, uid):
        url = 'https://keybase.io/_/api/1.0/user/lookup.json'
        params = {
            'uids': uid,
        }

        r = requests.get(url, params)
        if r.status_code != 200:
            raise KeybaseNetworkGraphException('request_userdata() received a non http 200 response', r.content)

        return r.json()

    def out(self, message):
        print(message)


KeybaseNetworkGraph().main()
