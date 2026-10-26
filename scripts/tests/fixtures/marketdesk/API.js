let API = {

	speed: async function (ts) {

		let image = new Image();
		let random = Math.random().toString(36).substr(2, 10);

		image.src = `https://${ts}.${window.location.host}?${random}`;

		let timeStart = Date.now();
		let timeClose = Date.now();

		return new Promise((resolve, reject) => {
			
			image.addEventListener('load', (event) => {

				timeClose = Date.now();

				resolve(ts);
			});

		});
	},

	get: async function (endpoint, params = null) {

		try {

			let response = await axios.get(endpoint, {
				params: params,
				headers: {
					'Content-Type': 'application/json',
				}
			});
			return response.data;

		} catch (error) {

			console.info(error);
		}
	},

	post: async function (endpoint, payload = {}, params = null) {

		try {

			let response = await axios.post(endpoint, payload, {
				params: params
			});
			return response.data;

		} catch (error) {

			console.info(error);
		}
	},

	put: async function (endpoint, payload = {}, params = null) {

		try {

			let response = await axios.put(endpoint, payload, {
				params: params,
				headers: {
					'Content-Type': 'application/json',
				}
			});
			return response.data;

		} catch (error) {

			console.info(error);
		}
	},

	del: async function (endpoint, params = null) {

		try {

			let response = await axios.delete(endpoint, {
				params: params,
				headers: {
					'Content-Type': 'application/json',
				}
			});
			return response.data;

		} catch (error) {

			console.info(error);
		}
	},

	dragging: {

		data: function (dataTransfer, payload = false) {

			if (payload !== false) {
				dataTransfer.setData('application/json', JSON.stringify(payload));
			}

			try {
				return JSON.parse(dataTransfer.getData('application/json'));
			} catch (error) {
				return null;
			}
		},

		files: function (dataTransfer) {

			let filter = {
				kind: 'file',
				type: 'application/pdf'
			};

			return _.chain(dataTransfer.items)
				.filter(filter)
				.invokeMap('getAsFile')
				.map((file) => {

					return {
						blob: file,
						name: file.name,
						size: file.size,
						type: file.type
					}
				})
				.value();
		}
	},

	downloadFolder: async function (folderPathId) {

		
	},

	updateTimestamps: function () {

		document.querySelectorAll('[time]').forEach((el) => {

			const timestamp = parseInt(el.getAttribute('time'), 10);
			const dateTime = luxon.DateTime.fromSeconds(timestamp);
			const now = luxon.DateTime.now();

			// Calculate time difference
			const diffInHours = now.diff(dateTime, 'hours').hours;
			let output = ``;

			if (diffInHours <= 24) {
				// Get relative time string
				let relativeTime = dateTime.toRelative({locale:'en'}); // e.g., "1 minute ago"

				// Shorten long time units
				const replacements = {
					'seconds': 'sec',
					'minutes': 'min',
					'hours': 'hr',
					'days': 'd',
					'weeks': 'w',
					'months': 'mo',
					'years': 'y',
					'second': 'sec',
					'minute': 'min',
					'hour': 'hr',
					'day': 'd',
					'week': 'w',
					'month': 'mo',
					'year': 'y'
				};

				for (let [long, short] of Object.entries(replacements)) {
					relativeTime = relativeTime.replace(long, short);
				}

				output = relativeTime;
			} else {
				output = dateTime.toFormat('MMM d');
			}

			el.textContent = `${output}`;
			el.setAttribute('formatted', `${output}`);
		});

	}


};


setInterval(API.updateTimestamps, 200);