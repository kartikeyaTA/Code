import { useState } from "react";
import Chat from "./components/chat.jsx";
import texasImg from "./assets/texas-roadhouse.jpg";
import { FaUser, FaTimes } from "react-icons/fa";
import ContactCard from "./components/contactCard.jsx";
import Contact1 from './assets/contact1.png';
import Contact2 from './assets/contact2.png';
import Contact3 from './assets/contact3.png';
import Accordion from "./components/accordian.jsx";

function App() {
  const [showDashboard, setShowDashboard] = useState(false);

  const contactData = [
    {
      "url": Contact1,
      "title": "Career Opportunities",
      "description": "How would you like to become a Roadie? We work hard and play hard. Discover our employment opportunities.",
      "button_title": "Explore Careers"
    },
    {
      "url": Contact2,
      "title": "Texas Road House Employees and vendors",
      "description": "If you are a Roadie or vendor partner and have a concern that you would like to report, please access our employee relations and vendor hotline by clicking below.",
      "button_title": "EMPLOYEES & VENDORS"
    },
    {
      "url": Contact3,
      "title": "Check Your Gift Card Balance",
      "description": "Need to check your gift card balance?",
      "button_title": "CHECK YOUR BALANCE"
    }
  ];

  return (
    <>
      <div className="w-full">
        <div>
          <div className="min-h-screen bg-center bg-cover bg-no-repeat">
            <div className="flex justify-between w-full mx-auto p-4 bg-black fixed top-0 z-40">
              <h1 className="text-2xl font-bold text-[#004B2B]">
                TEXAS ROADHOUSE
              </h1>
              <div className="flex gap-[10px] items-center">
                {/* Toggle dashboard modal on the same page */}
                <button 
                  onClick={() => setShowDashboard(true)}
                  className="border rounded-[9999px] p-1.5 bg-[#004B2B] text-white hover:text-[#FFC72C] text-l border-white cursor-pointer transition-all duration-200 hover:border-[#FFC72C]"
                >
                  Dashboard
                </button>
                <button className="flex items-center justify-center border rounded-[50%] h-[40px] w-[40px] bg-[#004B2B] text-white transition-all duration-200 hover:text-[#FFC72C]">
                  <FaUser className="text-lg" />
                </button>
              </div>
            </div>

            <img className="w-full h-[650px] pt-[70px]" src={texasImg} alt="" />

            <div className="bg-[#c9912d] h-[750px] mb-[50px]">
              <div className="w-[92%] mx-auto flex gap-[2.5%] text-center mt-[50px]">
                {contactData.map((item, index) => (
                  <ContactCard key={index} item={item} />
                ))}
              </div>
            </div>

            <div className="flex flex-col items-center justify-center">
              <h1 className="text-3xl font-bold text-[#a5022f]">Frequently Asked Questions</h1>
              <div className="mt-5 text-sm">
                <Accordion />
              </div>
            </div>

            <Chat />
          </div>
        </div>
      </div>

      {/* --- DASHBOARD MODAL --- */}
      {showDashboard && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
          <div className="bg-white w-full max-w-5xl h-[85vh] rounded-lg shadow-2xl flex flex-col overflow-hidden relative">
            {/* Modal Header */}
            <div className="bg-[#004B2B] text-white p-3 flex justify-between items-center">
              <h2 className="font-bold text-lg">Container App Dashboard</h2>
              <button 
                onClick={() => setShowDashboard(false)}
                className="text-white hover:text-[#FFC72C] text-xl p-1"
              >
                <FaTimes />
              </button>
            </div>

            {/* Embedded Container App Content */}
            <div className="flex-1 w-full h-full bg-gray-100">
              <iframe
                src="https://txrh-ca-test.greenmeadow-610a0edf.eastus.azurecontainerapps.io/chat"
                className="w-full h-full border-none"
                title="Container App Dashboard"
              />
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export default App;