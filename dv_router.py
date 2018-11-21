import sim.api as api
import sim.basics as basics

from dv_utils import PeerTable, PeerTableEntry, ForwardingTable, \
    ForwardingTableEntry

# We define infinity as a distance of 16.
INFINITY = 16

# A route should time out after at least 15 seconds.
ROUTE_TTL = 15


class DVRouter(basics.DVRouterBase):
    # NO_LOG = True  # Set to True on an instance to disable its logging.
    # POISON_MODE = True  # Can override POISON_MODE here.
    # DEFAULT_TIMER_INTERVAL = 5  # Can override this yourself for testing.

    def __init__(self):
        """
        Called when the instance is initialized.

        DO NOT remove any existing code from this method.
        """
        self.start_timer()  # Starts calling handle_timer() at correct rate.

        # Maps a port to the latency of the link coming out of that port.
        self.link_latency = {}

        # Maps a port to the PeerTable for that port.
        # Contains an entry for each port whose link is up, and no entries
        # for any other ports.
        self.peer_tables = {}

        # Forwarding table for this router (constructed from peer tables).
        self.forwarding_table = ForwardingTable()

        self.history = {}

        self.removedHosts = []
        self.emptyFwdTableList = []
        self.poisonedHosts = []
        self.removedHostKeys = []
        self.removedFromPoisonedHosts = []
        self.trickledHosts = []

    def add_static_route(self, host, port):
        """
        Adds a static route to a host directly connected to this router.

        Called automatically by the framework whenever a host is connected
        to this router.

        :param host: the host.
        :param port: the port that the host is attached to.
        :returns: nothing.
        """
        # `port` should have been added to `peer_tables` by `handle_link_up`
        # when the link came up.
        assert port in self.peer_tables, "Link is not up?"
        dst = host
        latency = 0
        expire_time = PeerTableEntry.FOREVER
        pte = PeerTableEntry(dst, latency, expire_time)
        self.peer_tables[port][host] = pte
        self.update_forwarding_table()
        self.send_routes(force=False)

    def handle_link_up(self, port, latency):
        """
        Called by the framework when a link attached to this router goes up.

        :param port: the port that the link is attached to.
        :param latency: the link latency.
        :returns: nothing.
        """
        self.link_latency[port] = latency
        self.peer_tables[port] = PeerTable()

        for key in self.forwarding_table.keys():
            adPacket = basics.RoutePacket(key, self.forwarding_table.get(key).latency)
            self.send(adPacket, port, flood=False)
            fte = ForwardingTableEntry(key, port, self.forwarding_table.get(key).latency)
            self.history[(port, key)] = fte

    def handle_link_down(self, port):
        """
        Called by the framework when a link attached to this router does down.

        :param port: the port number used by the link.
        :returns: nothing.
        """
        for x in self.forwarding_table.keys():
            if self.forwarding_table[x][1] == port:
                if ([self.forwarding_table[x][0], port]) not in self.removedHosts:
                    self.removedHosts += [[self.forwarding_table[x][0], port]]
                    self.poisonedHosts += [[self.forwarding_table[x][0], port]]
                    del self.forwarding_table[x]

        if (port in self.peer_tables.keys()):
            del self.peer_tables[port]

        for elem in self.removedHosts:
            for item in self.history.keys():
                if elem[1] != item[0] and elem[0] == item[1]:
                    del self.history[item]
        
        self.update_forwarding_table()
        self.send_routes(force=False)

    def handle_route_advertisement(self, dst, port, route_latency):
        """
        Called when the router receives a route advertisement from a neighbor.

        :param dst: the destination of the advertised route.
        :param port: the port that the advertisement came from.
        :param route_latency: latency from the neighbor to the destination.
        :return: nothing.
        """
        currentPTE = PeerTableEntry(dst, route_latency, api.current_time() + ROUTE_TTL)
        self.peer_tables[port][dst] = currentPTE

        self.update_forwarding_table()
        self.send_routes(force=False)
        
    def update_forwarding_table(self):
        """
        Computes and stores a new forwarding table merged from all peer tables.

        :returns: nothing.
        """
        self.forwarding_table.clear()  # First, clear the old forwarding table.
        
        for port, peer_table in self.peer_tables.items():
            currCost = 0
            key = None
            for host, value in peer_table.items():
                key = host
                if (self.peer_tables.get(port)):
                    currCost = self.link_latency[port] + value.latency
                else:
                    currCost = self.link_latency[port]

                if (currCost > INFINITY): #Stage 3: Cap route latency at INFINITY when you create a ForwardingTableEntry 
                    currCost = INFINITY

                if (self.forwarding_table.get(key)):
                    prevCost = self.forwarding_table.get(key).latency
                    if (currCost < prevCost):
                        prevCost = currCost
                        fwdTableEntry = ForwardingTableEntry(key, port, prevCost)
                        self.forwarding_table[key] = fwdTableEntry

                else:
                    fwdTableEntry = ForwardingTableEntry(key, port, currCost)
                    self.forwarding_table[key] = fwdTableEntry


    def handle_data_packet(self, packet, in_port):
        """
        Called when a data packet arrives at this router.

        You may want to forward the packet, drop the packet, etc. here.

        :param packet: the packet that arrived.
        :param in_port: the port from which the packet arrived.
        :return: nothing.
        """
        destination = packet.dst
        if (not self.forwarding_table.get(destination)):
            return
        dist = self.forwarding_table.get(destination).latency
        if (dist >= INFINITY):
            return
        out_port = self.forwarding_table.get(destination).port
        if (out_port == in_port):
            return
        self.send(packet, out_port, flood=False)

    def send_routes(self, force=False):
        """
        Send route advertisements for all routes in the forwarding table.

        :param force: if True, advertises ALL routes in the forwarding table;
                      otherwise, advertises only those routes that have
                      changed since the last advertisement.
        :return: nothing.
        """
        for port, peer_table in self.peer_tables.items():
            for key, item in self.forwarding_table.items():
                fwdPorts = item.port

                if (self.POISON_MODE):
                    if (port == fwdPorts):
                        if force == False:
                            if (port, key) not in self.history.keys() or (self.history[(port, key)][0] != key or self.history[(port, key)][2] != INFINITY):
                                adPacket = basics.RoutePacket(key, INFINITY)
                                self.send(adPacket, port, flood=False)
                                fte = ForwardingTableEntry(key, port, INFINITY)
                                self.history[(port, key)] = fte
                        else:
                            adPacket = basics.RoutePacket(key, INFINITY)
                            self.send(adPacket, port, flood=False)
                            fte = ForwardingTableEntry(key, port, INFINITY)
                            self.history[(port, key)] = fte

                    else:
                        if force == False:
                            if (port, key) not in self.history.keys() or (self.history[(port, key)][0] != key or self.history[(port, key)][2] != item.latency):
                                adPacket = basics.RoutePacket(key, item.latency)
                                self.send(adPacket, port, flood=False)
                                fte = ForwardingTableEntry(key, port, item.latency)
                                self.history[(port, key)] = fte

                        else:
                            adPacket = basics.RoutePacket(key, item.latency)
                            self.send(adPacket, port, flood=False)
                            fte = ForwardingTableEntry(key, port, item.latency)
                            self.history[(port, key)] = fte

                elif (not self.POISON_MODE and port != fwdPorts):
                    adPacket = basics.RoutePacket(key, item.latency)
                    if force == False:
                        if (port, key) not in self.history.keys() or (self.history[(port, key)][0] != key or self.history[(port, key)][2] != item.latency):
                            self.send(adPacket, port, flood=False)
                            fte = ForwardingTableEntry(key, port, item.latency)
                            self.history[(port, key)] = fte
                    else:
                        self.send(adPacket, port, flood=False)
                        fte = ForwardingTableEntry(key, port, item.latency)
                        self.history[(port, key)] = fte

        tempList = []
        if (self.POISON_MODE):
            for rem in self.removedHosts:
                for port in self.peer_tables.keys():
                    host = rem[0]

                    if (force == False):
                        if rem not in self.poisonedHosts:
                            break

                    if (force == True):
                        if (port, host) in self.history.keys():
                            val = self.history[(port, host)]
                            if val[2] != INFINITY:

                                if (len(self.forwarding_table) == 0):
                                    if (port, host) in self.history.keys() and (port, host) not in self.emptyFwdTableList:
                                        self.emptyFwdTableList += [(port, host)]
                                    elif (port, host) in self.emptyFwdTableList:
                                        break

                                for key, value in self.forwarding_table.items():
                                    if host != key and (port, host) in self.history.keys():
                                        del self.history[(port, host)]
                                    elif (host == key):
                                        tempList += [(port, key)]

                            else:

                                if (len(self.forwarding_table) == 0):
                                    if (port, host) in self.history.keys() and (port, host) not in self.emptyFwdTableList:
                                        self.emptyFwdTableList += [(port, host)]
                                    elif (port, host) in self.emptyFwdTableList:
                                        break

                                for key, value in self.forwarding_table.items():
                                    if (key == host and value[1] == port):
                                        tempList += [(port, key)]
                                    else:
                                        if ((port, host) in self.history.keys()):
                                            if [host, port] in self.removedFromPoisonedHosts:
                                                pass
                                            else:
                                                del self.history[(port, host)]

                    if ((port, host) not in self.history.keys() or self.emptyFwdTableList):                        
                        if (port, host) in tempList:
                            continue

                        if [host, port] in self.removedFromPoisonedHosts:
                            continue

                        if [host, port] in self.trickledHosts:
                            continue

                        adPacket = basics.RoutePacket(host, INFINITY)
                        self.send(adPacket, port, flood=False)
                        fte = ForwardingTableEntry(host, port, INFINITY)
                        self.history[(port, host)] = fte

        if (self.POISON_MODE):
            if (force == False):
                for elem in self.poisonedHosts:
                    if elem in self.removedHosts:
                        self.poisonedHosts.remove(elem)
                        self.removedFromPoisonedHosts += [elem]

    def expire_routes(self):
        """
        Clears out expired routes from peer tables; updates forwarding table
        accordingly.
        """
        for port, peer_table in self.peer_tables.items():
            for host, value in peer_table.items():
                if (value.expire_time == PeerTableEntry.FOREVER):
                    return
                if (api.current_time() > value.expire_time):

                    for dest, fwdEntry in self.forwarding_table.items():
                        if (dest == host):
                            if [dest, port] not in self.removedHosts:
                                self.removedHosts += [[dest, port]]
                                self.poisonedHosts += [[dest, port]]
                                                        
                            del self.peer_tables[port][dest]
                    
                    self.update_forwarding_table()


    def handle_timer(self):
        """
        Called periodically.

        This function simply calls helpers to clear out expired routes and to
        send the forwarding table to neighbors.
        """
        self.expire_routes()
        self.send_routes(force=True)

    # Feel free to add any helper methods!