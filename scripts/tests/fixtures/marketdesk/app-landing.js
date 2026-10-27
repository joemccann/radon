let waitForElement = async function (selector) {

	await Promise.delay(25);
	
	if (document.querySelector(selector)) {
		return Promise.delay(25);
	} else {
		return waitForElement(selector);
	}

}


let firebaseConfig = {
	apiKey: "AIzaSyCf8fXJkGM9Jgtzl6sdaMLe_ipwtmWdvNc",  // gitleaks:allow
	authDomain: "zerohedge-prod.firebaseapp.com",
	databaseURL: "https://zerohedge-prod.firebaseio.com",
	projectId: "zerohedge-prod",
	storageBucket: "zerohedge-prod.appspot.com",
	messagingSenderId: "777690811792",
	appId: "1:777690811792:web:2f257ff9245dbe8d51b715",
	measurementId: "G-1Z38D8VXNY"
};

firebase.initializeApp(firebaseConfig);
let ui = new firebaseui.auth.AuthUI(firebase.auth());
let uiConfig = {

	callbacks: {

		signInSuccessWithAuthResult: function (authResult, redirectUrl) {

			console.info(authResult);

			firebase.auth().currentUser.getIdToken()
				.then((token) => {

					return API.post('/api/user/login', {F: token});
				})
				.then((result) => {

					if (result.success === true) {
						window.location.assign('/');
					}
				});

			return false;
		},

		signInFailure: function (error) {

		},

		uiShown: async function () {

			await waitForElement('.firebaseui-card-header .firebaseui-title');
			document.querySelector('.firebaseui-card-header .firebaseui-title').innerHTML = ``;
			document.querySelector('#firebaseui-auth-container').classList.toggle('unset', false);
			document.querySelector('#firebaseui-auth-container').classList.toggle('active', true);

			let cancelButton = document.querySelector('#firebaseui-auth-container .firebaseui-id-page-sign-in .firebaseui-id-secondary-link.mdl-button--primary');

			cancelButton.replaceWith(cancelButton.cloneNode(true));
			cancelButton = document.querySelector('#firebaseui-auth-container .firebaseui-id-page-sign-in .firebaseui-id-secondary-link.mdl-button--primary');

		}
	},
	signInFlow: 'popup',
	signInSuccessUrl: '/',
	signInOptions: [
		firebase.auth.EmailAuthProvider.PROVIDER_ID,
		firebase.auth.GoogleAuthProvider.PROVIDER_ID
	],
	tosUrl: 'https://marketdesk.ai/terms',
	privacyPolicyUrl: 'https://www.zerohedge.com/cookie-policy'
};
ui.start('#firebaseui-auth-container', uiConfig);


