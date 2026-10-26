/**
* WebSocket connections
**/
let Wire = (function (libs) {

	let internals = {

		retries: 0,

		reload: function (data) {

			window.location.reload(true);
		},

		onServerOpen: function () {

			console.info('Server open');
			this.events['connect'].forEach((callback) => { callback(); });
			internals.retries = 0;
		},

		onServerClose: function (reason) {

			console.info('Server close', reason);
			this.events['disconnect'].forEach((callback) => { callback(reason); });
			setTimeout(() => this.reconnect(), 5000 * internals.retries++);
		},

		onServerMessage: function (message) {

			message = message.data;

			try {
				message = JSON.parse(message);
			} catch (e) {
				message = {};
			}

			this.events['message'].forEach((callback) => { callback(message.data); });
			
			if (this.events.hasOwnProperty(message.type) === true) {
				this.events[message.type].forEach((callback) => { callback(message.data); });
			}
		},

		onServerError: function (error) {

			console.info('Server error', error);
			setTimeout(() => this.reconnect(), 5000 * internals.retries++);
		}

	};


	let api = function (url) {

		this.events = {
			'connect': [],
			'disconnect': [],
			'message': [],
			'page.reload': [internals.reload]
		};
		this.url = url;
		this.settings = {};
		this.connection = new libs.ws(this.url, [], this.settings);
		this.connection.onopen = internals.onServerOpen.bind(this);
		this.connection.onclose = internals.onServerClose.bind(this);
		this.connection.onmessage = internals.onServerMessage.bind(this);
		this.connection.onerror = internals.onServerError.bind(this);

		this.queue = [];
	};

	api.prototype.send = function (type, data) {

		let payload = JSON.stringify({
			type: type,
			data: data
		});

		try {
			this.connection.send(payload);
		} catch (error) {
			this.queue.push({
				type: type,
				data: data
			});
		}
	};

	api.prototype.ping = function () {

		try {
			this.connection.send('ping');
		} catch (error) {
			// Ping failed
		}
	}

	api.prototype.on = function (type, callback) {

		if (this.events.hasOwnProperty(type) === false) {
			this.events[type] = [];
		}
		this.events[type].push(callback);
	};

	api.prototype.reconnect = function () {

		this.connection.onopen = null;
		this.connection.onclose = null;
		this.connection.onmessage = null;
		this.connection.onerror = null;
		this.connection.close();

		this.connection = new libs.ws(this.url, [], this.settings);
		this.connection.onopen = internals.onServerOpen.bind(this);
		this.connection.onclose = internals.onServerClose.bind(this);
		this.connection.onmessage = internals.onServerMessage.bind(this);
		this.connection.onerror = internals.onServerError.bind(this);
	};

	api.prototype.disconnect = function () {

		this.connection.close();
	};

	return api;

})({
	ws: WebSocket
});

